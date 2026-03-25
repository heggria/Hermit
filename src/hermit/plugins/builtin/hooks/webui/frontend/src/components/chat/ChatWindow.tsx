import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { MessageBubble } from "./MessageBubble";
import { ToolExecution } from "./ToolExecution";
import type { ChatMessage } from "@/hooks/useWebSocket";

interface ChatWindowProps {
  messages: ChatMessage[];
  isResponding: boolean;
}

interface ToolPair {
  start: ChatMessage;
  complete?: ChatMessage;
}

type GroupedEntry =
  | { kind: "bubble"; data: ChatMessage }
  | { kind: "tool"; data: ToolPair }
  | { kind: "error"; data: ChatMessage }
  | { kind: "approved"; data: ChatMessage };

function groupMessages(messages: ChatMessage[]): GroupedEntry[] {
  const result: GroupedEntry[] = [];
  const pendingTools = new Map<string, number>();

  for (const msg of messages) {
    switch (msg.type) {
      case "user":
      case "response": {
        result.push({ kind: "bubble", data: msg });
        break;
      }
      case "tool_start": {
        const toolName = msg.name ?? "unknown";
        const pair: ToolPair = { start: msg };
        result.push({ kind: "tool", data: pair });
        pendingTools.set(toolName, result.length - 1);
        break;
      }
      case "tool_complete": {
        const toolName = msg.name ?? "unknown";
        const idx = pendingTools.get(toolName);
        if (idx !== undefined) {
          const entry = result[idx];
          if (entry.kind === "tool") {
            (entry.data as ToolPair).complete = msg;
          }
          pendingTools.delete(toolName);
        }
        break;
      }
      case "error": {
        result.push({ kind: "error", data: msg });
        break;
      }
      case "approved": {
        result.push({ kind: "approved", data: msg });
        break;
      }
      default:
        break;
    }
  }

  return result;
}

export function ChatWindow({ messages, isResponding }: ChatWindowProps) {
  const { t } = useTranslation();
  const bottomRef = useRef<HTMLDivElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to bottom on new messages
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const grouped = groupMessages(messages);

  return (
    <div ref={containerRef} className="flex-1 overflow-y-auto">
      {grouped.length === 0 ? (
        /* Empty state: centered welcome */
        <div className="flex h-full flex-col items-center justify-center px-4">
          <div className="flex size-16 items-center justify-center rounded-2xl bg-primary/10 mb-6">
            <span className="text-2xl font-bold text-primary">H</span>
          </div>
          <h2 className="text-xl font-semibold text-foreground mb-2">
            {t("chat.welcome")}
          </h2>
          <p className="text-sm text-muted-foreground text-center max-w-sm">
            {t("chat.welcomeSubtext")}
          </p>
        </div>
      ) : (
        <div className="mx-auto max-w-3xl px-4 py-6">
          <div className="flex flex-col gap-4">
            {grouped.map((entry, i) => {
              switch (entry.kind) {
                case "bubble": {
                  const msg = entry.data as ChatMessage;
                  return (
                    <div
                      key={msg.id}
                      className="animate-message-in"
                    >
                      <MessageBubble message={msg} />
                    </div>
                  );
                }
                case "tool": {
                  const pair = entry.data as ToolPair;
                  return (
                    <div
                      key={pair.start.id}
                      className="animate-message-in"
                    >
                      <ToolExecution
                        startMessage={pair.start}
                        completeMessage={pair.complete}
                      />
                    </div>
                  );
                }
                case "error": {
                  const msg = entry.data as ChatMessage;
                  return (
                    <div
                      key={msg.id}
                      className="animate-message-in flex justify-start"
                    >
                      <div className="max-w-[85%] rounded-2xl rounded-bl-sm border border-destructive/20 bg-destructive/5 px-4 py-3">
                        <p className="text-sm text-destructive">
                          {msg.message ?? t("chat.errorOccurred")}
                        </p>
                      </div>
                    </div>
                  );
                }
                case "approved": {
                  const msg = entry.data as ChatMessage;
                  return (
                    <div
                      key={msg.id}
                      className="animate-message-in flex justify-start"
                    >
                      <div className="max-w-[85%] rounded-2xl rounded-bl-sm border border-green-200 bg-green-50 px-4 py-3 dark:border-green-800 dark:bg-green-950">
                        <p className="whitespace-pre-wrap break-words text-sm text-green-800 dark:text-green-200">
                          {msg.text ?? t("chat.approved")}
                        </p>
                      </div>
                    </div>
                  );
                }
                default:
                  return <div key={i} />;
              }
            })}

            {/* Thinking indicator */}
            {isResponding && (
              <div className="flex justify-start animate-message-in">
                <div className="rounded-2xl rounded-bl-sm bg-card px-4 py-3 shadow-sm">
                  <div className="flex items-center gap-1.5">
                    <div className="size-1.5 rounded-full bg-muted-foreground/60 animate-dot-pulse" style={{ animationDelay: "0ms" }} />
                    <div className="size-1.5 rounded-full bg-muted-foreground/60 animate-dot-pulse" style={{ animationDelay: "200ms" }} />
                    <div className="size-1.5 rounded-full bg-muted-foreground/60 animate-dot-pulse" style={{ animationDelay: "400ms" }} />
                  </div>
                </div>
              </div>
            )}

            <div ref={bottomRef} />
          </div>
        </div>
      )}
    </div>
  );
}
