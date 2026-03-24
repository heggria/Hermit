import { useState, useEffect, useRef, useCallback } from "react";

export interface ChatMessage {
  id: string;
  type:
    | "tool_start"
    | "tool_complete"
    | "response"
    | "error"
    | "heartbeat"
    | "approved"
    | "user";
  name?: string;
  inputs?: Record<string, unknown>;
  result?: string;
  text?: string;
  is_command?: boolean;
  message?: string;
  timestamp: number;
}

interface UseWebSocketReturn {
  messages: ChatMessage[];
  connected: boolean;
  send: (msg: Record<string, unknown>) => void;
  clearMessages: () => void;
  addMessage: (msg: ChatMessage) => void;
}

const RECONNECT_DELAY = 3000;

let messageCounter = 0;
function nextId(): string {
  messageCounter += 1;
  return `msg-${Date.now()}-${messageCounter}`;
}

export function useWebSocket(url: string): UseWebSocketReturn {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const urlRef = useRef(url);

  useEffect(() => {
    urlRef.current = url;
  }, [url]);

  const clearMessages = useCallback(() => {
    setMessages([]);
  }, []);

  const addMessage = useCallback((msg: ChatMessage) => {
    setMessages((prev) => [...prev, msg]);
  }, []);

  const send = useCallback((msg: Record<string, unknown>) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(msg));
    }
  }, []);

  useEffect(() => {
    function connect() {
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }

      const ws = new WebSocket(urlRef.current);
      wsRef.current = ws;

      ws.onopen = () => {
        setConnected(true);
      };

      ws.onclose = () => {
        setConnected(false);
        wsRef.current = null;
        reconnectTimerRef.current = setTimeout(connect, RECONNECT_DELAY);
      };

      ws.onerror = () => {
        ws.close();
      };

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data) as Record<string, unknown>;
          const msgType = data.type as string;

          if (msgType === "heartbeat") {
            return;
          }

          const chatMessage: ChatMessage = {
            id: nextId(),
            type: msgType as ChatMessage["type"],
            name: data.name as string | undefined,
            inputs: data.inputs as Record<string, unknown> | undefined,
            result: data.result as string | undefined,
            text: data.text as string | undefined,
            is_command: data.is_command as boolean | undefined,
            message: data.message as string | undefined,
            timestamp: Date.now(),
          };

          setMessages((prev) => [...prev, chatMessage]);
        } catch {
          // Ignore malformed messages
        }
      };
    }

    connect();

    return () => {
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
      }
      if (wsRef.current) {
        wsRef.current.onclose = null;
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, []);

  return { messages, connected, send, clearMessages, addMessage };
}
