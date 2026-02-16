import { useState, useEffect, useRef, useCallback, type KeyboardEvent } from "react";
import { Link } from "react-router-dom";
import { useAuthContext } from "../contexts/AuthContext";
import type { ChatMessage } from "../types";

const MAX_BACKOFF_MS = 30_000;   // 30 seconds max delay
const BASE_DELAY_MS = 1_000;     // 1 second initial delay

export default function Chat() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [inputText, setInputText] = useState("");
  const [isConnected, setIsConnected] = useState(false);
  const [isStreaming, setIsStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { getAccessToken } = useAuthContext();
  const [isReconnecting, setIsReconnecting] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const reconnectAttemptRef = useRef(0);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const scheduleReconnectRef = useRef<(closeCode?: number) => void>(() => {});
  const hasParseErrorRef = useRef(false);

  const scrollToBottom = useCallback(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, []);

  useEffect(() => {
    scrollToBottom();
  }, [messages, scrollToBottom]);

  const connect = useCallback(() => {
    const token = getAccessToken();
    if (!token) {
      setError("Not authenticated");
      return;
    }

    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${proto}//${window.location.host}/ws/chat`;
    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      ws.send(JSON.stringify({ type: "auth", token }));
      setIsConnected(true);
      setError(null);
      // Reset reconnection state on successful connection
      reconnectAttemptRef.current = 0;
      setIsReconnecting(false);
    };

    ws.onmessage = (event) => {
      let data: Record<string, unknown>;
      try {
        data = JSON.parse(event.data);
      } catch {
        console.error("Failed to parse WebSocket message:", event.data);
        setError("Failed to parse server message");
        hasParseErrorRef.current = true;
        return;
      }
      // Clear transient parse error only when one is active (avoid per-message state churn)
      if (hasParseErrorRef.current) {
        hasParseErrorRef.current = false;
        setError(null);
      }
      const type = data.type as string | undefined;
      switch (type) {
        case "token":
          setMessages((prev) => {
            const updated = [...prev];
            const last = updated[updated.length - 1];
            if (last && last.role === "assistant") {
              updated[updated.length - 1] = {
                ...last,
                content: last.content + (typeof data.text === "string" ? data.text : ""),
              };
            }
            return updated;
          });
          setIsStreaming(true);
          break;
        case "sources":
          setMessages((prev) => {
            const updated = [...prev];
            const last = updated[updated.length - 1];
            if (last && last.role === "assistant") {
              updated[updated.length - 1] = {
                ...last,
                sources: Array.isArray(data.memory_ids) ? data.memory_ids as string[] : [],
              };
            }
            return updated;
          });
          break;
        case "done":
          setMessages((prev) => {
            const updated = [...prev];
            const last = updated[updated.length - 1];
            if (last && last.role === "assistant") {
              updated[updated.length - 1] = { ...last, isStreaming: false };
            }
            return updated;
          });
          setIsStreaming(false);
          break;
        case "error":
          setError(typeof data.detail === "string" ? data.detail : "An error occurred");
          setIsStreaming(false);
          break;
      }
    };

    ws.onclose = (event) => {
      setIsConnected(false);
      wsRef.current = null;
      if (event.code === 4001) {
        setError("Session expired — please re-login");
      } else if (event.code !== 1000) {
        if (event.code === 4003) {
          setError("AI services are starting up. Reconnecting...");
        }
        scheduleReconnectRef.current(event.code);
      }
    };

    ws.onerror = () => {
      setError("WebSocket connection failed");
      setIsConnected(false);
    };
  }, [getAccessToken]);

  const scheduleReconnect = useCallback((closeCode?: number) => {
    if (closeCode === 4001) return;
    if (reconnectTimerRef.current) return;
    const attempt = reconnectAttemptRef.current + 1;
    reconnectAttemptRef.current = attempt;
    const delay = Math.min(BASE_DELAY_MS * Math.pow(2, attempt - 1), MAX_BACKOFF_MS);
    const jitteredDelay = delay * (0.5 + Math.random() * 0.5);
    setIsReconnecting(true);
    reconnectTimerRef.current = setTimeout(() => {
      reconnectTimerRef.current = null;
      connect();
    }, jitteredDelay);
  }, [connect]);

  // Keep ref in sync so connect's onclose can call scheduleReconnect without circular dependency
  useEffect(() => {
    scheduleReconnectRef.current = scheduleReconnect;
  }, [scheduleReconnect]);

  const cancelReconnectAndConnect = useCallback(() => {
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
    reconnectAttemptRef.current = 0;
    setIsReconnecting(false);
    setError(null);
    connect();
  }, [connect]);

  useEffect(() => {
    connect();
    return () => {
      // Clear any pending reconnect timer
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
      wsRef.current?.close(1000);
      wsRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleSend = useCallback(() => {
    const text = inputText.trim();
    if (!text || !isConnected || isStreaming || !wsRef.current) return;

    const userMsg: ChatMessage = {
      id: crypto.randomUUID(),
      role: "user",
      content: text,
    };
    const assistantMsg: ChatMessage = {
      id: crypto.randomUUID(),
      role: "assistant",
      content: "",
      isStreaming: true,
    };
    setMessages((prev) => [...prev, userMsg, assistantMsg]);
    wsRef.current.send(JSON.stringify({ type: "question", text, top_k: 5 }));
    setInputText("");
    setIsStreaming(true);
  }, [inputText, isConnected, isStreaming]);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        handleSend();
      }
    },
    [handleSend],
  );

  return (
    <div className="h-full flex flex-col">
      {/* Connection status */}
      <div className="flex items-center justify-between px-3 sm:px-4 py-2 border-b border-gray-800">
        <h2 className="text-lg font-semibold text-gray-100">Chat</h2>
        <div className="flex items-center gap-2">
          {isReconnecting ? (
            <>
              <div className="w-2.5 h-2.5 rounded-full bg-yellow-500 animate-pulse" />
              <span className="text-xs text-yellow-400">
                Reconnecting (attempt {reconnectAttemptRef.current})...
              </span>
            </>
          ) : (
            <>
              <div
                className={`w-2.5 h-2.5 rounded-full ${
                  isConnected ? "bg-green-500" : "bg-red-500"
                }`}
              />
              <span className="text-xs text-gray-500">
                {isConnected ? "Connected" : "Disconnected"}
              </span>
            </>
          )}
        </div>
      </div>

      {/* Error banner */}
      {error && (
        <div className="px-4 py-2 bg-red-900/30 border-b border-red-800 flex items-center justify-between">
          <p className="text-red-400 text-sm">{error}</p>
          {!isConnected && (
            <button
              onClick={cancelReconnectAndConnect}
              className="text-sm text-blue-400 hover:text-blue-300 ml-3"
            >
              Reconnect
            </button>
          )}
        </div>
      )}

      {/* Message area */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {messages.length === 0 && (
          <div className="flex flex-col items-center justify-center h-full text-center">
            <p className="text-gray-400 text-lg mb-2">
              Ask your brain anything...
            </p>
            <p className="text-gray-500 text-sm">
              Your memories are searched and cited in every answer.
            </p>
          </div>
        )}

        {messages.map((msg) => (
          <div
            key={msg.id}
            className={`${
              msg.role === "user" ? "ml-8 sm:ml-12" : "mr-8 sm:mr-12"
            }`}
          >
            <div
              className={`rounded-lg p-3 ${
                msg.role === "user"
                  ? "bg-gray-800 text-gray-100"
                  : "bg-gray-900 border border-gray-800 text-gray-200"
              }`}
            >
              <p className="whitespace-pre-wrap">
                {msg.content}
                {msg.isStreaming && (
                  <span className="animate-pulse">&#9612;</span>
                )}
              </p>

              {/* Source citations */}
              {msg.role === "assistant" &&
                msg.sources &&
                msg.sources.length > 0 &&
                !msg.isStreaming && (
                  <div className="mt-3 pt-2 border-t border-gray-800">
                    <p className="text-xs text-gray-500 mb-1">Sources:</p>
                    <div className="flex flex-wrap gap-2">
                      {msg.sources.map((id) => (
                        <Link
                          key={id}
                          to={`/memory/${id}`}
                          className="text-sm text-blue-400 hover:text-blue-300"
                        >
                          Memory {id.slice(0, 8)}...
                        </Link>
                      ))}
                    </div>
                  </div>
                )}
            </div>
          </div>
        ))}
        <div ref={messagesEndRef} />
      </div>

      {/* Input area */}
      <div className="border-t border-gray-800 p-3 sm:p-4 bg-gray-950 sticky bottom-0">
        <div className="flex gap-3">
          <textarea
            ref={textareaRef}
            value={inputText}
            onChange={(e) => setInputText(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Type your question..."
            rows={1}
            className="flex-1 bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 sm:px-4 sm:py-3 text-gray-100 resize-none focus:ring-2 focus:ring-blue-500 focus:outline-none"
          />
          <button
            onClick={handleSend}
            disabled={!inputText.trim() || !isConnected || isStreaming}
            className="px-4 py-3 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white rounded-lg transition-colors"
          >
            Send
          </button>
        </div>
      </div>
    </div>
  );
}
