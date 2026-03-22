import { formatRuntimeConfigSource, RuntimeConfig } from './runtime-config-schema';

type AuthDiagnosticCardProps = {
  runtimeConfig: RuntimeConfig;
  loading?: boolean;
};

function envValue(value: string | number | boolean | null | undefined) {
  if (typeof value === 'boolean') {
    return value ? 'true' : 'false';
  }

  if (typeof value === 'number') {
    return String(value);
  }

  return value && String(value).trim() ? String(value) : 'unset';
}

export default function AuthDiagnosticCard({ runtimeConfig, loading = false }: AuthDiagnosticCardProps) {
  const healthUrl = runtimeConfig.apiUrl ? `${runtimeConfig.apiUrl}/health` : null;

  return (
    <article className="dataCard authDiagnosticCard">
      <p className="sectionEyebrow">Operator diagnostics</p>
      <h2>Auth runtime configuration</h2>
      <p className="muted">Use this before escalating: it shows the server-resolved runtime auth config for the current deployment.</p>
      <div className="kvGrid compactKvGrid authDiagnosticGrid">
        <p>
          <span>authTransport</span>
          {loading ? 'loading…' : 'same-origin proxy'}
        </p>
        <p>
          <span>backendApiUrl</span>
          {loading ? 'loading…' : envValue(runtimeConfig.apiUrl)}
        </p>
        <p>
          <span>configured</span>
          {loading ? 'loading…' : envValue(runtimeConfig.configured)}
        </p>
        <p>
          <span>apiUrl</span>
          {loading ? 'loading…' : envValue(runtimeConfig.apiUrl)}
        </p>
        <p>
          <span>liveModeEnabled</span>
          {loading ? 'loading…' : envValue(runtimeConfig.liveModeEnabled)}
        </p>
        <p>
          <span>apiTimeoutMs</span>
          {loading ? 'loading…' : envValue(runtimeConfig.apiTimeoutMs)}
        </p>
        <p>
          <span>source</span>
          {loading ? 'loading…' : formatRuntimeConfigSource(runtimeConfig.source)}
        </p>
        <p>
          <span>diagnostic</span>
          {loading ? 'Loading runtime configuration…' : envValue(runtimeConfig.diagnostic)}
        </p>
      </div>
      {runtimeConfig.diagnostic?.includes('localhost as API base URL') ? (
        <p className="statusLine">Warning: this deployment is serving a localhost API URL, which is invalid in production and will break the backend auth proxy.</p>
      ) : null}
      {healthUrl ? (
        <a className="authDiagnosticLink" href={healthUrl} target="_blank" rel="noreferrer">
          Open /health
        </a>
      ) : (
        <p className="statusLine">/health is unavailable until the deployment runtime config resolves a valid backend API URL for the same-origin auth proxy.</p>
      )}
    </article>
  );
}
