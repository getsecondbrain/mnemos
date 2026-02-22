import { useState, useEffect, useRef, useCallback, type KeyboardEvent } from "react";
import { Link } from "react-router-dom";
import { useAuthContext } from "../contexts/AuthContext";
import { listConversations, getConversationMessages, deleteConversation } from "../services/api";
import type { ChatMessage, Conversation } from "../types";

const MAX_BACKOFF_MS = 30_000;   // 30 seconds max delay
const BASE_DELAY_MS = 1_000;     // 1 second initial delay

export default function Chat() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [inputText, setInputText] = useState("");
  const [isConnected, setIsConnected] = useState(false);
  const [isStreaming, setIsStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const { getAccessToken } = useAuthContext();
  const [isReconnecting, setIsReconnecting] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const reconnectAttemptRef = useRef(0);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const scheduleReconnectRef = useRef<(closeCode?: number) => void>(() => {});
  const hasParseErrorRef = useRef(false);
  const activeConversationIdRef = useRef<string | null>(null);

  // Keep ref in sync with state for use in callbacks
  useEffect(() => {
    activeConversationIdRef.current = activeConversationId;
  }, [activeConversationId]);

  const scrollToBottom = useCallback(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, []);

  useEffect(() => {
    scrollToBottom();
  }, [messages, scrollToBottom]);

  // Load conversation list on mount
  const refreshConversationList = useCallback(async () => {
    try {
      const convos = await listConversations({ limit: 50 });
      setConversations(convos);
    } catch {
      // Non-critical — sidebar just won't populate
    }
  }, []);

  useEffect(() => {
    refreshConversationList();
  }, [refreshConversationList]);

  const connect = useCallback((conversationId?: string | null) => {
    const token = getAccessToken();
    if (!token) {
      setError("Not authenticated");
      return;
    }

    // Close any existing connection
    if (wsRef.current) {
      wsRef.current.onclose = null;
      wsRef.current.close(1000);
      wsRef.current = null;
    }

    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${proto}//${window.location.host}/ws/chat`;
    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      const authPayload: Record<string, string> = { type: "auth", token };
      if (conversationId) {
        authPayload.conversation_id = conversationId;
      }
      ws.send(JSON.stringify(authPayload));
      setIsConnected(true);
      setError(null);
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
      if (hasParseErrorRef.current) {
        hasParseErrorRef.current = false;
        setError(null);
      }
      const type = data.type as string | undefined;
      switch (type) {
        case "conversation_ready":
          if (typeof data.conversation_id === "string") {
            setActiveConversationId(data.conversation_id);
          }
          break;
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
        case "title_update": {
          const convId = typeof data.conversation_id === "string" ? data.conversation_id : "";
          const newTitle = typeof data.title === "string" ? data.title : "";
          if (convId && newTitle) {
            setConversations((prev) => {
              const existing = prev.find((c) => c.id === convId);
              if (existing) {
                return prev.map((c) =>
                  c.id === convId ? { ...c, title: newTitle, updated_at: new Date().toISOString() } : c
                );
              }
              return [
                {
                  id: convId,
                  title: newTitle,
                  created_at: new Date().toISOString(),
                  updated_at: new Date().toISOString(),
                },
                ...prev,
              ];
            });
          }
          break;
        }
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
      connect(activeConversationIdRef.current);
    }, jitteredDelay);
  }, [connect]);

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
    connect(activeConversationIdRef.current);
  }, [connect]);

  useEffect(() => {
    connect();
    return () => {
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
      if (wsRef.current) {
        wsRef.current.onclose = null;
        wsRef.current.close(1000);
        wsRef.current = null;
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleSelectConversation = useCallback(async (convId: string) => {
    if (convId === activeConversationIdRef.current) {
      setSidebarOpen(false);
      return;
    }

    setLoadingHistory(true);
    setError(null);
    try {
      const msgs = await getConversationMessages(convId);
      const chatMessages: ChatMessage[] = msgs.map((m) => ({
        id: m.id,
        role: m.role,
        content: m.content,
        sources: m.sources ?? undefined,
        isStreaming: false,
      }));
      setMessages(chatMessages);
      setActiveConversationId(convId);

      // Reconnect WebSocket to resume this conversation
      connect(convId);
    } catch {
      setError("Failed to load conversation history");
    } finally {
      setLoadingHistory(false);
      setSidebarOpen(false);
    }
  }, [connect]);

  const handleNewChat = useCallback(() => {
    setMessages([]);
    setActiveConversationId(null);
    setError(null);
    connect();
    setSidebarOpen(false);
  }, [connect]);

  const handleDeleteConversation = useCallback(async (convId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    try {
      await deleteConversation(convId);
      setConversations((prev) => prev.filter((c) => c.id !== convId));
      if (activeConversationIdRef.current === convId) {
        handleNewChat();
      }
    } catch {
      setError("Failed to delete conversation");
    }
  }, [handleNewChat]);

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

  const activeTitle = conversations.find((c) => c.id === activeConversationId)?.title ?? "Chat";

  return (
    <div className="h-full flex">
      {/* Sidebar overlay (mobile) */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 bg-black/50 z-30 md:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* Sidebar */}
      <div
        className={`${
          sidebarOpen ? "translate-x-0" : "-translate-x-full"
        } md:translate-x-0 fixed md:relative z-40 md:z-auto w-60 h-full bg-gray-950 border-r border-gray-800 flex flex-col transition-transform duration-200`}
      >
        <div className="p-3 border-b border-gray-800">
          <button
            onClick={handleNewChat}
            className="w-full px-3 py-2 text-sm bg-gray-800 hover:bg-gray-700 text-gray-200 rounded-lg transition-colors"
          >
            + New chat
          </button>
        </div>
        <div className="flex-1 overflow-y-auto">
          {conversations.map((conv) => (
            <div
              key={conv.id}
              onClick={() => handleSelectConversation(conv.id)}
              className={`group flex items-center gap-2 px-3 py-2.5 cursor-pointer text-sm border-b border-gray-900 hover:bg-gray-900 transition-colors ${
                conv.id === activeConversationId
                  ? "bg-gray-800/60 text-gray-100"
                  : "text-gray-400"
              }`}
            >
              <span className="flex-1 truncate">{conv.title}</span>
              <button
                onClick={(e) => handleDeleteConversation(conv.id, e)}
                className="opacity-0 group-hover:opacity-100 text-gray-600 hover:text-red-400 transition-opacity shrink-0"
                title="Delete conversation"
              >
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                </svg>
              </button>
            </div>
          ))}
          {conversations.length === 0 && (
            <p className="text-gray-600 text-xs text-center mt-4 px-3">No conversations yet</p>
          )}
        </div>
      </div>

      {/* Main chat area */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Header */}
        <div className="flex items-center justify-between px-3 sm:px-4 py-2 border-b border-gray-800">
          <div className="flex items-center gap-2 min-w-0">
            <button
              onClick={() => setSidebarOpen(!sidebarOpen)}
              className="md:hidden text-gray-400 hover:text-gray-200 shrink-0"
            >
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
              </svg>
            </button>
            <h2 className="text-lg font-semibold text-gray-100 truncate">
              {activeTitle}
            </h2>
          </div>
          <div className="flex items-center gap-2">
            {loadingHistory && (
              <span className="text-xs text-blue-400">Loading...</span>
            )}
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
    </div>
  );
}
