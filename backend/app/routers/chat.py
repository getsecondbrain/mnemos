"""Chat API — WebSocket endpoint for streaming RAG chat.

Authenticates via an initial auth message carrying a JWT access token,
then enters a message loop where the client sends questions and the
server streams tokens back in real-time with source memory IDs.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sqlmodel import Session

from app import auth_state
from app.db import get_session
from app.models.conversation import Conversation
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


def _clean_title(raw: str) -> str:
    """Strip quotes, trailing punctuation, and validate 2-80 char length."""
    title = raw.strip()
    # Strip surrounding quotes (single, double, backtick)
    title = re.sub(r'^[\"\'\`]+|[\"\'\`]+$', '', title).strip()
    # Remove trailing sentence punctuation
    title = title.rstrip('.,;:!?')
    # Collapse whitespace
    title = re.sub(r'\s+', ' ', title).strip()
    # Validate length
    if len(title) < 2:
        return "New conversation"
    if len(title) > 80:
        return title[:77] + "..."
    return title


async def _handle_question(
    websocket: WebSocket,
    rag_service: RAGService,
    text: str,
    top_k: int,
) -> str:
    """Run RAG stream_query and send tokens + sources + done to the client.

    Returns the full accumulated assistant response text.
    """
    token_stream, source_ids = await rag_service.stream_query(text, top_k=top_k)

    chunks: list[str] = []
    async for token in token_stream:
        await websocket.send_json({"type": "token", "text": token})
        chunks.append(token)

    await websocket.send_json({"type": "sources", "memory_ids": source_ids})
    await websocket.send_json({"type": "done"})

    return "".join(chunks)


def _persist_exchange(
    db_session: Session,
    conversation: Conversation,
    user_text: str,
    assistant_text: str,
) -> tuple[Conversation, bool]:
    """Save exchange to conversation and return (conversation, needs_ai_title).

    Returns needs_ai_title=True when the conversation title is still the
    default "New conversation" — indicating the first exchange just completed
    and a title should be generated.
    """
    needs_ai_title = conversation.title == "New conversation"
    conversation.updated_at = datetime.now(timezone.utc)
    db_session.add(conversation)
    db_session.commit()
    db_session.refresh(conversation)
    return conversation, needs_ai_title


async def _generate_title(
    websocket: WebSocket,
    llm_service: LLMService,
    conversation_id: str,
    user_text: str,
    assistant_text: str,
) -> None:
    """Generate a conversation title from the first exchange via LLM.

    Runs as a fire-and-forget asyncio.create_task. Creates its own DB
    session to avoid sharing the WebSocket handler's session across
    concurrent coroutines.
    """
    # Truncate inputs to keep the prompt short
    user_snippet = user_text[:200]
    assistant_snippet = assistant_text[:300]

    prompt = (
        f"Generate a short, descriptive title (2-6 words) for this conversation.\n\n"
        f"User: {user_snippet}\n"
        f"Assistant: {assistant_snippet}"
    )
    system = (
        "You generate short conversation titles. "
        "Output ONLY the title text — no quotes, no extra punctuation, no explanation."
    )

    try:
        response = await llm_service.generate(
            prompt=prompt,
            system=system,
            temperature=0.3,
        )
        title = _clean_title(response.text)

        # If cleaning returned the default, skip the update
        if title == "New conversation":
            return

        # Update DB in a fresh session (safe for background task)
        from app.db import engine
        with Session(engine) as session:
            conv = session.get(Conversation, conversation_id)
            if conv is not None:
                conv.title = title
                conv.updated_at = datetime.now(timezone.utc)
                session.add(conv)
                session.commit()

        # Send title update to client via WebSocket
        await websocket.send_json({
            "type": "title_update",
            "conversation_id": conversation_id,
            "title": title,
        })
    except WebSocketDisconnect:
        logger.debug(
            "Client disconnected before title_update could be sent (conv=%s)",
            conversation_id,
        )
    except Exception:
        logger.warning(
            "Failed to generate title for conversation %s",
            conversation_id,
            exc_info=True,
        )


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

    # --- Create conversation record ---
    conversation = Conversation()
    db_session.add(conversation)
    db_session.commit()
    db_session.refresh(conversation)

    title_task_fired = False  # Guard to prevent duplicate title generation tasks

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
                assistant_text = await _handle_question(
                    websocket, rag_service, text, top_k
                )

                # Persist exchange and check if title generation is needed
                conversation, needs_ai_title = _persist_exchange(
                    db_session, conversation, text, assistant_text
                )

                # Fire title generation for first exchange
                if needs_ai_title and not title_task_fired:
                    title_task_fired = True
                    asyncio.create_task(
                        _generate_title(
                            websocket,
                            llm_service,
                            conversation.id,
                            text,
                            assistant_text,
                        )
                    )
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
