import { cn } from "@/lib/utils";
import type { ChatMessage } from "@/hooks/useWebSocket";

interface MessageBubbleProps {
  message: ChatMessage;
}

function formatTime(timestamp: number): string {
  const date = new Date(timestamp);
  return date.toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function MessageBubble({ message }: MessageBubbleProps) {
  const isUser = message.type === "user";
  const text = message.text ?? message.message ?? "";

  return (
    <div
      className={cn(
        "flex w-full",
        isUser ? "justify-end" : "justify-start",
      )}
    >
      <div className="flex flex-col gap-1">
        <div
          className={cn(
            "px-4 py-3 text-sm leading-relaxed",
            isUser
              ? "max-w-[70%] rounded-2xl rounded-br-sm bg-primary text-primary-foreground"
              : "max-w-[85%] rounded-2xl rounded-bl-sm bg-card text-card-foreground shadow-sm",
          )}
        >
          <p className="whitespace-pre-wrap break-words">{text}</p>
        </div>
        <span
          className={cn(
            "text-[10px] text-muted-foreground/60 px-1",
            isUser ? "text-right" : "text-left",
          )}
        >
          {formatTime(message.timestamp)}
        </span>
      </div>
    </div>
  );
}
