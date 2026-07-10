'use client';

// Real-time telemetry SSE client. Mirrors app/alert-stream-client.ts: native
// EventSource cannot send Authorization + X-Workspace-Id, so this uses
// fetch() + ReadableStream with automatic reconnect + backoff. Points at the
// /api/stream/telemetry proxy (→ backend /stream/telemetry → workspace
// :telemetry Redis stream). Reconnect is signalled via onStatusChange so the
// page can run a one-shot recovery refetch and keep periodic HTTP polling as the
// fallback while disconnected.

export type TelemetryStreamEvent = {
  eventId: string;
  payload: unknown;
};

export type TelemetryStreamStatus = 'live' | 'reconnecting' | 'disconnected';

export type TelemetryStreamCallbacks = {
  onConnected: () => void;
  onEvent: (event: TelemetryStreamEvent) => void;
  onHeartbeat: () => void;
  onStatusChange: (status: TelemetryStreamStatus) => void;
};

const SSE_PROXY_PATH = '/api/stream/telemetry';
// Exponential reconnect backoff: base delay, doubling each failed attempt, capped.
// Reset to the base as soon as a connection succeeds so a single blip does not push
// the next retry to the ceiling. RECONNECT_DELAY_MS stays the base (referenced by
// the telemetry-realtime-stream spec).
const RECONNECT_DELAY_MS = 1000;
const MAX_RECONNECT_DELAY_MS = 15000;

export function reconnectDelayMs(attempt: number): number {
  const exp = RECONNECT_DELAY_MS * 2 ** Math.max(0, attempt);
  return Math.min(exp, MAX_RECONNECT_DELAY_MS);
}

export function parseSseLine(line: string): { field: string; value: string } | null {
  if (!line) return null;
  if (line.startsWith(':')) {
    const raw = line.slice(1);
    return { field: 'comment', value: raw.startsWith(' ') ? raw.slice(1) : raw };
  }
  const colonIdx = line.indexOf(':');
  if (colonIdx === -1) {
    return { field: line, value: '' };
  }
  const rawValue = line.slice(colonIdx + 1);
  return {
    field: line.slice(0, colonIdx),
    value: rawValue.startsWith(' ') ? rawValue.slice(1) : rawValue,
  };
}

export function connectTelemetryStream(
  headers: Record<string, string>,
  callbacks: TelemetryStreamCallbacks,
): () => void {
  const abortController = new AbortController();
  let closed = false;
  let lastEventId: string | undefined;
  // Reset to 0 on every successful connect so backoff only grows across a run of
  // consecutive failures, never permanently after one recovered blip.
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
      response = await fetch(SSE_PROXY_PATH, {
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
      const delay = reconnectDelayMs(reconnectAttempt);
      reconnectAttempt += 1;
      await new Promise<void>((resolve) => {
        const t = setTimeout(resolve, delay);
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
