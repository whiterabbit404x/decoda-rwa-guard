import type { BuildInfo, BuildInfoDiagnostics } from './build-info';
import { formatRuntimeConfigSource, RuntimeConfig } from './runtime-config-schema';

const DEFAULT_AUTH_MODE_LABEL = 'same-origin proxy';

type AuthDiagnosticCardProps = {
  runtimeConfig: RuntimeConfig;
  buildInfo: BuildInfo;
  buildInfoDiagnostics: BuildInfoDiagnostics;
  currentHost?: string | null;
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

export default function AuthDiagnosticCard({
  runtimeConfig,
  buildInfo,
  buildInfoDiagnostics,
  currentHost = null,
  loading = false,
}: AuthDiagnosticCardProps) {
  const healthUrl = runtimeConfig.apiUrl ? `${runtimeConfig.apiUrl}/health` : null;

  return (
    <article className="dataCard authDiagnosticCard">
      <div className="authDiagnosticHeader">
        <div>
          <p className="sectionEyebrow">Operator diagnostics</p>
          <h2>Auth runtime configuration</h2>
        </div>
        <span className="pill authVersionBadge">build {buildInfoDiagnostics.versionLabel}</span>
      </div>
      <p className="muted">Use this before escalating: it shows the server-resolved runtime auth config and deployment metadata for the current auth UI.</p>
      {buildInfoDiagnostics.warnings.map((warning) => (
        <p key={warning} className="statusLine authDiagnosticWarning">{warning}</p>
      ))}
      <div className="kvGrid compactKvGrid authDiagnosticGrid">
        <p>
          <span>deploymentEnvironment</span>
          {envValue(buildInfo.vercelEnv)}
        </p>
        <p>
          <span>currentHost</span>
          {envValue(currentHost)}
        </p>
        <p>
          <span>vercelUrl</span>
          {envValue(buildInfo.vercelUrl)}
        </p>
        <p>
          <span>gitCommitSha</span>
          {envValue(buildInfo.gitCommitShaShort)}
        </p>
        <p>
          <span>gitBranch</span>
          {envValue(buildInfo.gitBranch)}
        </p>
        <p>
          <span>authTransport</span>
          {buildInfo.authMode ?? DEFAULT_AUTH_MODE_LABEL}
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
          <span>liveModeEnabled</span>
          {loading ? 'loading…' : envValue(runtimeConfig.liveModeEnabled)}
        </p>
        <p>
          <span>nodeEnv</span>
          {envValue(buildInfo.nodeEnv)}
        </p>
        <p>
          <span>buildTimestamp</span>
          {envValue(buildInfo.buildTimestamp)}
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
