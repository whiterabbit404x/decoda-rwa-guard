import {
  type SystemHealthFetchResult,
  diagnoseSystemHealthFailure,
} from './fetch-system-health';

type Props = {
  result: Extract<SystemHealthFetchResult, { ok: false }>;
  lastSuccessfulFetchAt?: string | null;
};

function DiagnosticRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="shMetricRow">
      <span className="shMetricLabel">{label}</span>
      <span className="shMetricValue" style={{ fontFamily: 'var(--font-mono, monospace)' }}>
        {value}
      </span>
    </div>
  );
}

/**
 * Single, polished SaaS error panel rendered when the system-health API itself
 * cannot be reached. It deliberately does NOT render the 8 component cards as
 * "Unavailable" — an endpoint failure means we cannot know component status, so
 * we surface diagnostics and a retry instead of fabricating an all-down state.
 */
export function SystemHealthEndpointError({ result, lastSuccessfulFetchAt }: Props) {
  const diagnosis = diagnoseSystemHealthFailure(result);

  return (
    <section
      className="dataCard shHero shActionCallout-danger"
      style={{ marginTop: '1rem' }}
      data-testid="system-health-endpoint-error"
    >
      <div className="shHeroTop">
        <div
          className="healthIcon healthIconOffline"
          style={{ width: '3.5rem', height: '3.5rem', fontSize: '1.5rem', flexShrink: 0 }}
        >
          ✕
        </div>

        <div className="shHeroMain">
          <div className="shHeroBadgeRow">
            <span className="statusBadge statusBadge-offline shHeroStatusBadge">
              Endpoint unreachable
            </span>
            <span className="statusBadge statusBadge-unavailable shEnvBadge">
              {diagnosis.category.replace(/_/g, ' ')}
            </span>
          </div>
          <p className="shHeroSummary">{diagnosis.headline}</p>
          <p className="explanation small" style={{ marginTop: '0.35rem' }}>
            Component status is unknown while the health API is unreachable. This panel does not
            represent an all-systems outage — it means the dashboard could not read live health.
          </p>
        </div>

        <div className="shHeroMeta">
          <a href="/system-health" className="secondaryCta" style={{ fontSize: '0.85rem' }}>
            Retry
          </a>
        </div>
      </div>

      <div className="shMetricsGrid" style={{ marginTop: '1rem' }}>
        <DiagnosticRow label="Requested endpoint" value={result.url} />
        <DiagnosticRow label="HTTP status" value={result.status != null ? String(result.status) : 'No response'} />
        <DiagnosticRow label="Error" value={diagnosis.detail} />
        <DiagnosticRow
          label="Last successful fetch"
          value={
            lastSuccessfulFetchAt
              ? new Date(lastSuccessfulFetchAt).toLocaleString()
              : 'Not tracked in this environment'
          }
        />
      </div>

      <div className="shActionCallout shActionCallout-warning" style={{ marginTop: '1rem' }}>
        <span className="shActionCalloutIcon shActionCalloutIcon-warning">⚠</span>
        <div>
          <strong className="shActionCalloutTitle shActionCalloutTitle-warning">Suggested action</strong>
          <p className="shActionCalloutBody shActionCalloutBody-warning">{diagnosis.suggestedAction}</p>
        </div>
      </div>
    </section>
  );
}
