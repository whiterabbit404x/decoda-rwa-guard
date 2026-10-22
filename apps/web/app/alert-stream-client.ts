'use client';

// SSE client using fetch() + ReadableStream because native EventSource
// does not support custom headers (required for Authorization + X-Workspace-Id).

export type AlertStreamEvent = {
  eventId: string;
  payload: unknown;
};

export type AlertStreamStatus = 'live' | 'reconnecting' | 'polling-fallback' | 'disconnected';

export type AlertStreamCallbacks = {
  onConnected: () => void;
  onEvent: (event: AlertStreamEvent) => void;
  onHeartbeat: () => void;
  onStatusChange: (status: AlertStreamStatus) => void;
};

const SSE_PROXY_PATH = '/api/stream/alerts';
// First retry after RECONNECT_DELAY_MS, doubling to the ceiling while the stream
// keeps refusing, and back to the base once a connection is established. A fixed
// 3s retry against a stream that answers 503 was ~20 requests a minute from every
// open tab -- the most frequent request the product made -- while the live feed's
// own polling fallback was already carrying the screen.
const RECONNECT_DELAY_MS = 3000;
const MAX_RECONNECT_DELAY_MS = 60_000;

export function alertStreamReconnectDelayMs(attempt: number): number {
  return Math.min(RECONNECT_DELAY_MS * 2 ** Math.max(0, attempt), MAX_RECONNECT_DELAY_MS);
}

export function parseSseLine(line: string): { field: string; value: string } | null {
  if (!line) return null;
  if (line.startsWith(':')) {
    // Per SSE spec: strip at most one leading space after the colon.
    const raw = line.slice(1);
    return { field: 'comment', value: raw.startsWith(' ') ? raw.slice(1) : raw };
  }
  const colonIdx = line.indexOf(':');
  if (colonIdx === -1) {
    return { field: line, value: '' };
  }
  // Per SSE spec: strip at most one leading space from the value field.
  const rawValue = line.slice(colonIdx + 1);
  return {
    field: line.slice(0, colonIdx),
    value: rawValue.startsWith(' ') ? rawValue.slice(1) : rawValue,
  };
}

export function connectAlertStream(
  headers: Record<string, string>,
  callbacks: AlertStreamCallbacks,
  proxyPath: string = SSE_PROXY_PATH,
): () => void {
  const abortController = new AbortController();
  let closed = false;
  let lastEventId: string | undefined;
  let reconnectAttempt = 0;

  async function connectOnce(): Promise<boolean> {
    const requestHeaders: Record<string, string> = {
      ...headers,
      Accept: 'text/event-stream',
      'Cache-Control': 'no-cache',
    };
    if (lastEventId) {
      requestHeaders['Last-Event-ID'] = lastEventId;
    }

    let response: Response;
    try {
      response = await fetch(proxyPath, {
        headers: requestHeaders,
        signal: abortController.signal,
        cache: 'no-store',
      });
    } catch (err) {
      if (err instanceof Error && err.name === 'AbortError') {
        return false;
      }
      return true; // transient error, retry
    }

    if (!response.ok || !response.body) {
      return true; // retry
    }

    reconnectAttempt = 0;
    callbacks.onConnected();
    callbacks.onStatusChange('live');

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let currentEventId: string | undefined;
    let currentData: string | undefined;

    try {
      while (!closed) {
        let readResult: ReadableStreamReadResult<Uint8Array>;
        try {
          readResult = await reader.read();
        } catch (err) {
          if (err instanceof Error && err.name === 'AbortError') {
            return false;
          }
          return true;
        }

        if (readResult.done) break;
        const { value } = readResult;
        if (!value) continue;

        buffer += decoder.decode(value, { stream: true });

        let newlineIdx: number;
        while ((newlineIdx = buffer.indexOf('\n')) !== -1) {
          const rawLine = buffer.slice(0, newlineIdx);
          buffer = buffer.slice(newlineIdx + 1);
          const line = rawLine.endsWith('\r') ? rawLine.slice(0, -1) : rawLine;

          if (line === '') {
            // Blank line → dispatch the assembled event
            if (currentData !== undefined) {
              const eventId = currentEventId ?? `ts-${Date.now()}`;
              if (currentEventId) lastEventId = currentEventId;
              try {
                const payload = JSON.parse(currentData) as unknown;
                callbacks.onEvent({ eventId, payload });
              } catch {
                // Malformed JSON payload, skip
              }
            }
            currentEventId = undefined;
            currentData = undefined;
          } else {
            const parsed = parseSseLine(line);
            if (!parsed) continue;
            if (parsed.field === 'comment') {
              if (parsed.value === 'heartbeat') callbacks.onHeartbeat();
            } else if (parsed.field === 'id') {
              currentEventId = parsed.value;
            } else if (parsed.field === 'data') {
              currentData = parsed.value;
            }
          }
        }
      }
    } finally {
      try {
        reader.releaseLock();
      } catch {
        // Reader may already be released on error path
      }
    }

    return !closed;
  }

  async function runLoop(): Promise<void> {
    while (!closed) {
      const shouldRetry = await connectOnce();
      if (!shouldRetry || closed) break;
      callbacks.onStatusChange('reconnecting');
      const delay = alertStreamReconnectDelayMs(reconnectAttempt);
      reconnectAttempt += 1;
      await new Promise<void>((resolve) => {
        const t = setTimeout(resolve, delay);
        // Allow clean exit during reconnect delay
        abortController.signal.addEventListener('abort', () => { clearTimeout(t); resolve(); }, { once: true });
      });
    }
    if (!closed) {
      callbacks.onStatusChange('disconnected');
    }
  }

  void runLoop();

  return () => {
    closed = true;
    abortController.abort();
  };
}
