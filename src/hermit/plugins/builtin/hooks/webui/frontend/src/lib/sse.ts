// SSE EventSource wrapper with auto-reconnect for real-time kernel updates.

export function createEventSource(
  url: string,
  handlers: Record<string, (data: unknown) => void>,
  options?: { reconnectMs?: number },
): () => void {
  const reconnectMs = options?.reconnectMs ?? 3000;
  let es: EventSource | null = null;
  let active = true;

  function connect() {
    if (!active) return;
    es = new EventSource(url);

    for (const [event, handler] of Object.entries(handlers)) {
      es.addEventListener(event, (e: MessageEvent) => {
        try {
          handler(JSON.parse(e.data));
        } catch {
          // Ignore malformed events
        }
      });
    }

    es.onerror = () => {
      es?.close();
      if (active) setTimeout(connect, reconnectMs);
    };
  }

  connect();
  return () => {
    active = false;
    es?.close();
  };
}
