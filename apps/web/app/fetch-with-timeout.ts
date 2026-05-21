export class FetchTimeoutError extends Error {
  readonly timeoutMs: number;

  constructor(timeoutMs: number) {
    super(`Request timed out after ${timeoutMs}ms`);
    this.name = 'FetchTimeoutError';
    this.timeoutMs = timeoutMs;
  }
}

const DEFAULT_FETCH_TIMEOUT_MS = 10_000;

function resolveFiniteTimeout(timeoutMs?: number): number {
  if (typeof timeoutMs === 'number' && Number.isFinite(timeoutMs) && timeoutMs > 0) {
    return timeoutMs;
  }
  return DEFAULT_FETCH_TIMEOUT_MS;
}

export async function fetchWithTimeout(input: string | URL | Request, init: RequestInit = {}, timeoutMs?: number): Promise<Response> {
  const effectiveTimeoutMs = resolveFiniteTimeout(timeoutMs);
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), effectiveTimeoutMs);

  try {
    const response = await fetch(input, {
      ...init,
      signal: controller.signal,
    });
    return response;
  } catch (error) {
    if (error instanceof Error && error.name === 'AbortError') {
      throw new FetchTimeoutError(effectiveTimeoutMs);
    }
    throw error;
  } finally {
    clearTimeout(timeoutId);
  }
}
