export type RuntimeConfigValueSource =
  | 'API_URL'
  | 'NEXT_PUBLIC_API_URL'
  | 'LIVE_MODE_ENABLED'
  | 'NEXT_PUBLIC_LIVE_MODE_ENABLED'
  | 'API_TIMEOUT_MS'
  | 'NEXT_PUBLIC_API_TIMEOUT_MS'
  | 'default'
  | 'missing'
  | 'invalid';

export type RuntimeConfig = {
  apiUrl: string | null;
  liveModeEnabled: boolean;
  apiTimeoutMs: number | null;
  configured: boolean;
  diagnostic: string | null;
  source: {
    apiUrl: RuntimeConfigValueSource;
    liveModeEnabled: RuntimeConfigValueSource;
    apiTimeoutMs: RuntimeConfigValueSource;
  };
};

export function formatRuntimeConfigSource(source: RuntimeConfig['source']) {
  return `apiUrl=${source.apiUrl}, liveModeEnabled=${source.liveModeEnabled}, apiTimeoutMs=${source.apiTimeoutMs}`;
}
