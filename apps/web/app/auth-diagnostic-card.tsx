type AuthDiagnosticCardProps = {
  apiUrl: string;
  liveModeConfigured: boolean;
};

const LIVE_MODE_ENV_VALUE = process.env.NEXT_PUBLIC_LIVE_MODE_ENABLED ?? '';

function envValue(value: string | undefined) {
  return value && value.trim() ? value : 'unset';
}

export default function AuthDiagnosticCard({ apiUrl, liveModeConfigured }: AuthDiagnosticCardProps) {
  const healthUrl = apiUrl ? `${apiUrl}/health` : null;

  return (
    <article className="dataCard authDiagnosticCard">
      <p className="sectionEyebrow">Operator diagnostics</p>
      <h2>Auth environment snapshot</h2>
      <p className="muted">Use this before escalating: it shows which public web variables are loaded into the browser and provides a direct API health link.</p>
      <div className="kvGrid compactKvGrid authDiagnosticGrid">
        <p>
          <span>NEXT_PUBLIC_API_URL</span>
          {envValue(apiUrl)}
        </p>
        <p>
          <span>NEXT_PUBLIC_LIVE_MODE_ENABLED</span>
          {envValue(LIVE_MODE_ENV_VALUE)}
        </p>
        <p>
          <span>Live mode configured</span>
          {liveModeConfigured ? 'true' : 'false'}
        </p>
      </div>
      {healthUrl ? (
        <a className="authDiagnosticLink" href={healthUrl} target="_blank" rel="noreferrer">
          Open /health
        </a>
      ) : (
        <p className="statusLine">/health is unavailable until NEXT_PUBLIC_API_URL is configured.</p>
      )}
    </article>
  );
}
