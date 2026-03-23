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

type RuntimeConfigField = keyof RuntimeConfig['source'];

function sourceLabel(field: RuntimeConfigField) {
  switch (field) {
    case 'apiUrl':
      return 'backend API URL';
    case 'liveModeEnabled':
      return 'live mode flag';
    case 'apiTimeoutMs':
      return 'API timeout';
    default:
      return field;
  }
}

export function describeRuntimeConfigSource(field: RuntimeConfigField, source: RuntimeConfigValueSource) {
  switch (source) {
    case 'API_URL':
    case 'LIVE_MODE_ENABLED':
    case 'API_TIMEOUT_MS':
      return `${sourceLabel(field)} resolved from server runtime config`;
    case 'NEXT_PUBLIC_API_URL':
    case 'NEXT_PUBLIC_LIVE_MODE_ENABLED':
    case 'NEXT_PUBLIC_API_TIMEOUT_MS':
      return `${sourceLabel(field)} resolved from public runtime fallback`;
    case 'default':
      return `${sourceLabel(field)} is using the default value`;
    case 'missing':
      return `${sourceLabel(field)} is missing`;
    case 'invalid':
      return `${sourceLabel(field)} is invalid`;
    default:
      return `${sourceLabel(field)} source is unknown`;
  }
}

export function formatRuntimeConfigSource(source: RuntimeConfig['source']) {
  return [
    describeRuntimeConfigSource('apiUrl', source.apiUrl),
    describeRuntimeConfigSource('liveModeEnabled', source.liveModeEnabled),
    describeRuntimeConfigSource('apiTimeoutMs', source.apiTimeoutMs),
  ].join(' · ');
}
