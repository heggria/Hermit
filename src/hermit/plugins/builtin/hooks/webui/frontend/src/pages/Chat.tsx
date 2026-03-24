import { useState, useCallback, useMemo, useRef, type KeyboardEvent } from "react";
import { ArrowUp, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import { ChatWindow } from "@/components/chat/ChatWindow";
import { useWebSocket, type ChatMessage } from "@/hooks/useWebSocket";

function getWebSocketUrl(): string {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}/api/ws/chat`;
}

let userMsgCounter = 0;

export default function Chat() {
  const { t } = useTranslation();
  const [input, setInput] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const { messages, connected, send, clearMessages, addMessage } = useWebSocket(
    getWebSocketUrl(),
  );

  // Derive responding state from messages (no effect needed)
  const isResponding = useMemo(() => {
    if (messages.length === 0) return false;
    const lastMsg = messages[messages.length - 1];
    return lastMsg.type === "user" || lastMsg.type === "tool_start";
  }, [messages]);

  const handleSend = useCallback(() => {
    const text = input.trim();
    if (!text || !connected || isResponding) return;

    userMsgCounter += 1;
    const userMessage: ChatMessage = {
      id: `user-${Date.now()}-${userMsgCounter}`,
      type: "user",
      text,
      timestamp: Date.now(),
    };

    send({ type: "message", text });
    addMessage(userMessage);
    setInput("");

    // Reset textarea height
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
    }
  }, [input, connected, isResponding, send, addMessage]);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        handleSend();
      }
    },
    [handleSend],
  );

  // Auto-resize textarea
  const handleInput = useCallback(
    (e: React.ChangeEvent<HTMLTextAreaElement>) => {
      setInput(e.target.value);
      const el = e.target;
      el.style.height = "auto";
      const maxHeight = 4 * 24; // 4 lines approx
      el.style.height = `${Math.min(el.scrollHeight, maxHeight)}px`;
    },
    [],
  );

  const canSend = connected && input.trim().length > 0 && !isResponding;

  return (
    <div className="flex h-full flex-col">
      {/* Chat header */}
      <div className="flex items-center justify-between border-b border-border/50 px-6 py-3">
        <div className="flex items-center gap-3">
          <h1 className="text-lg font-semibold text-foreground">
            {t("chat.title")}
          </h1>
          <div className="flex items-center gap-1.5">
            <div
              className={cn(
                "size-2 rounded-full transition-colors",
                connected ? "bg-green-500" : "bg-muted-foreground/40",
              )}
            />
            <span className="text-xs text-muted-foreground">
              {connected ? t("chat.connected") : t("chat.disconnected")}
            </span>
          </div>
        </div>
        <button
          onClick={clearMessages}
          className="flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          title={t("chat.clear")}
        >
          <Trash2 className="size-3.5" />
          {t("chat.clear")}
        </button>
      </div>

      {/* Messages area */}
      <ChatWindow messages={messages} isResponding={isResponding} />

      {/* Input area -- fixed at bottom */}
      <div className="border-t border-border/50 bg-background px-4 py-3 sm:px-6">
        <div className="mx-auto max-w-3xl">
          <div className="relative flex items-end gap-2 rounded-2xl border border-border/60 bg-card px-4 py-2.5 shadow-sm transition-shadow focus-within:shadow-md focus-within:border-primary/30">
            <textarea
              ref={textareaRef}
              value={input}
              onChange={handleInput}
              onKeyDown={handleKeyDown}
              placeholder={
                connected
                  ? t("chat.placeholder")
                  : t("chat.placeholderDisabled")
              }
              disabled={!connected}
              rows={1}
              className="flex-1 resize-none bg-transparent text-sm leading-6 text-foreground placeholder:text-muted-foreground/60 focus:outline-none disabled:cursor-not-allowed disabled:opacity-50"
              style={{ maxHeight: `${4 * 24}px` }}
            />
            <button
              onClick={handleSend}
              disabled={!canSend}
              className={cn(
                "flex size-8 shrink-0 items-center justify-center rounded-full transition-all",
                canSend
                  ? "bg-primary text-primary-foreground hover:bg-primary/90 active:scale-95"
                  : "bg-muted text-muted-foreground/40 cursor-not-allowed",
              )}
              title={t("chat.send")}
            >
              <ArrowUp className="size-4" />
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
