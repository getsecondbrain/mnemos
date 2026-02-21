"""Chat API — WebSocket endpoint for streaming RAG chat.

Authenticates via an initial auth message carrying a JWT access token,
then enters a message loop where the client sends questions and the
server streams tokens back in real-time with source memory IDs.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sqlmodel import Session

from app import auth_state
from app.db import get_session
from app.routers.auth import _decode_token
from app.services.embedding import EmbeddingError, EmbeddingService
from app.services.encryption import EncryptionService
from app.services.llm import LLMError, LLMService
from app.services.owner_context import get_owner_context
from app.services.rag import RAGService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])


async def _authenticate(websocket: WebSocket) -> str:
    """Wait for the first message, validate JWT, and return session_id.

    Expected message format: {"type": "auth", "token": "<jwt_access_token>"}
    Sends an error and closes with code 4001 on failure.
    """
    try:
        raw = await websocket.receive_text()
        msg = json.loads(raw)
    except (json.JSONDecodeError, Exception) as exc:
        await websocket.send_json(
            {"type": "error", "detail": "Invalid auth message"}
        )
        await websocket.close(code=4001)
        raise WebSocketDisconnect(code=4001) from exc

    if msg.get("type") != "auth" or not msg.get("token"):
        await websocket.send_json(
            {"type": "error", "detail": "First message must be {type: 'auth', token: '...'}"}
        )
        await websocket.close(code=4001)
        raise WebSocketDisconnect(code=4001)

    try:
        payload = _decode_token(msg["token"], "access")
    except Exception:
        await websocket.send_json(
            {"type": "error", "detail": "Invalid or expired token"}
        )
        await websocket.close(code=4001)
        raise WebSocketDisconnect(code=4001)

    session_id = payload.get("sub")
    if not session_id:
        await websocket.send_json(
            {"type": "error", "detail": "Invalid token payload"}
        )
        await websocket.close(code=4001)
        raise WebSocketDisconnect(code=4001)

    return session_id


async def _handle_question(
    websocket: WebSocket,
    rag_service: RAGService,
    text: str,
    top_k: int,
) -> None:
    """Run RAG stream_query and send tokens + sources + done to the client."""
    token_stream, source_ids = await rag_service.stream_query(text, top_k=top_k)

    async for token in token_stream:
        await websocket.send_json({"type": "token", "text": token})

    await websocket.send_json({"type": "sources", "memory_ids": source_ids})
    await websocket.send_json({"type": "done"})


@router.websocket("/ws/chat")
async def chat_websocket(
    websocket: WebSocket,
    db_session: Session = Depends(get_session),
) -> None:
    """WebSocket endpoint for streaming RAG chat.

    Protocol:
    1. Client connects and sends {"type": "auth", "token": "<jwt>"}
    2. On success, enters message loop where client sends
       {"type": "question", "text": "...", "top_k": 5}
    3. Server streams {"type": "token", "text": "..."} messages
    4. Server sends {"type": "sources", "memory_ids": [...]}
    5. Server sends {"type": "done"}
    """
    await websocket.accept()

    # --- Authenticate ---
    try:
        session_id = await _authenticate(websocket)
    except WebSocketDisconnect:
        return

    # --- Verify session has a master key ---
    master_key = auth_state.get_master_key(session_id)
    if master_key is None:
        await websocket.send_json(
            {"type": "error", "detail": "Session expired, please re-authenticate"}
        )
        await websocket.close(code=4001)
        return

    # --- Construct services from app.state ---
    encryption_service = EncryptionService(master_key)

    embedding_service: EmbeddingService | None = getattr(
        websocket.app.state, "embedding_service", None
    )
    llm_service: LLMService | None = getattr(
        websocket.app.state, "llm_service", None
    )

    if embedding_service is None or llm_service is None:
        await websocket.send_json(
            {"type": "error", "detail": "AI services unavailable"}
        )
        await websocket.close(code=4003)
        return

    owner_name, family_context = get_owner_context(db_session)

    rag_service = RAGService(
        embedding_service=embedding_service,
        llm_service=llm_service,
        encryption_service=encryption_service,
        db_session=db_session,
        owner_name=owner_name,
        family_context=family_context,
    )

    # --- Message loop ---
    try:
        while True:
            raw = await websocket.receive_text()

            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json(
                    {"type": "error", "detail": "Invalid JSON"}
                )
                continue

            if msg.get("type") != "question" or not msg.get("text"):
                await websocket.send_json(
                    {"type": "error", "detail": "Expected {type: 'question', text: '...'}"}
                )
                continue

            text = msg["text"]
            top_k = msg.get("top_k", 5)
            top_k = max(1, min(20, int(top_k)))

            try:
                await _handle_question(websocket, rag_service, text, top_k)
            except (LLMError, EmbeddingError) as exc:
                logger.warning("Service error during chat: %s", exc)
                await websocket.send_json(
                    {"type": "error", "detail": str(exc)}
                )
            except Exception:
                logger.exception("Unexpected error during chat")
                await websocket.send_json(
                    {"type": "error", "detail": "Internal error processing question"}
                )
    except WebSocketDisconnect:
        logger.debug("WebSocket client disconnected (session=%s)", session_id)
