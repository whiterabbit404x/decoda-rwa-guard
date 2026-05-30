import Link from 'next/link';

export type ResponseActionState =
  | 'simulation_only'
  | 'manual_recommendation'
  | 'live_executable'
  | 'approval_required'
  | 'executed'
  | 'failed'
  | 'rolled_back';

export type ResponseAction = {
  id: string;
  label: string;
  state: ResponseActionState;
  disabled?: boolean;
  reason?: string;
  onClick?: () => void;
};

type Props = {
  capabilities: string[];
  actions: ResponseAction[];
  loading?: boolean;
  isSimulatorMode?: boolean;
  hasOpenAlerts?: boolean;
  hasOpenIncidents?: boolean;
};

const RESPONSE_ACTION_STATE_LABELS: Record<ResponseActionState, string> = {
  simulation_only: 'Simulation only',
  manual_recommendation: 'Manual recommendation',
  live_executable: 'Live executable',
  approval_required: 'Approval required',
  executed: 'Executed',
  failed: 'Failed',
  rolled_back: 'Rolled back',
};

type ActionCardProps = {
  href: string;
  title: string;
  helper: string;
  disabled?: boolean;
  disabledReason?: string;
};

function ActionCard({ href, title, helper, disabled, disabledReason }: ActionCardProps) {
  return (
    <div
      style={{
        flex: '1 1 220px',
        background: 'rgba(255,255,255,0.03)',
        border: '1px solid var(--border)',
        borderRadius: '12px',
        padding: '1.25rem 1.25rem 1rem',
        display: 'flex',
        flexDirection: 'column',
        gap: '0.5rem',
        opacity: disabled ? 0.55 : 1,
      }}
    >
      <p style={{ fontSize: '0.9375rem', fontWeight: 700, color: 'var(--text-primary)', margin: 0 }}>{title}</p>
      <p style={{ fontSize: '0.8125rem', color: 'var(--text-muted)', margin: 0, lineHeight: 1.5 }}>{helper}</p>
      {disabled ? (
        <span
          style={{ fontSize: '0.8125rem', color: 'var(--text-muted)', marginTop: 'auto', paddingTop: '0.5rem' }}
          title={disabledReason}
        >
          Unavailable
        </span>
      ) : (
        <Link
          href={href}
          prefetch={false}
          style={{
            display: 'inline-block',
            marginTop: 'auto',
            paddingTop: '0.5rem',
            fontSize: '0.875rem',
            fontWeight: 600,
            color: 'var(--accent)',
            textDecoration: 'none',
          }}
        >
          {title} →
        </Link>
      )}
    </div>
  );
}

export default function ResponseActionPanel({
  capabilities,
  actions,
  loading = false,
  isSimulatorMode = false,
  hasOpenAlerts = false,
  hasOpenIncidents = false,
}: Props) {
  const showNoActionMessage = !loading && actions.length === 0 && !isSimulatorMode && !hasOpenAlerts && !hasOpenIncidents;

  return (
    <article className="dataCard" aria-label="Response Actions" id="response-actions">
      <p className="sectionEyebrow">Response actions</p>
      <h3 style={{ fontSize: '1.125rem', fontWeight: 700, margin: '0 0 0.5rem' }}>Operational response</h3>
      {capabilities.length > 0 ? (
        <div className="chipRow" style={{ marginBottom: '1.25rem' }}>
          {capabilities.map((label) => (
            <span className="ruleChip" key={label}>{label}</span>
          ))}
        </div>
      ) : null}

      {showNoActionMessage ? (
        <p style={{ fontSize: '0.9375rem', color: 'var(--text-secondary)', marginBottom: '1.25rem' }}>
          No response action required. Monitoring is active and no open alert has escalated to an incident.
        </p>
      ) : null}

      {!loading && actions.length > 0 ? (
        <div role="status" aria-live="polite" style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem', marginBottom: '1.25rem' }}>
          {actions.map((action) => (
            <div
              key={action.id}
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                gap: '1rem',
                padding: '0.75rem 1rem',
                background: 'rgba(255,255,255,0.03)',
                border: '1px solid var(--border)',
                borderRadius: '8px',
              }}
            >
              <div>
                <p style={{ fontSize: '0.875rem', fontWeight: 600, color: 'var(--text-primary)', margin: '0 0 0.2rem' }}>{action.label}</p>
                <p style={{ fontSize: '0.8125rem', color: 'var(--text-muted)', margin: 0 }}>{RESPONSE_ACTION_STATE_LABELS[action.state]}</p>
              </div>
              <button
                type="button"
                disabled={action.disabled}
                title={action.reason}
                onClick={action.onClick}
                style={{ flexShrink: 0 }}
              >
                Execute
              </button>
            </div>
          ))}
        </div>
      ) : null}

      {!isSimulatorMode ? (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '1rem' }}>
          <ActionCard
            href="/alerts"
            title="Review alerts"
            helper="Inspect alert candidates and open alerts when escalation is required."
          />
          <ActionCard
            href="/incidents"
            title="Open incident queue"
            helper="Review active incident workflow and response ownership."
          />
          <ActionCard
            href="/monitoring-sources"
            title="Configure response policy"
            helper="Define escalation, evidence export, and response handling rules."
          />
          <ActionCard
            href="/exports"
            title="Export evidence"
            helper="Download evidence-ready records for audit, review, or customer reporting."
          />
        </div>
      ) : (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '1rem' }}>
          <ActionCard
            href="/alerts"
            title="Review alerts"
            helper="Inspect alert candidates and open alerts when escalation is required."
          />
          <ActionCard
            href="/incidents"
            title="Open incident queue"
            helper="Review active incident workflow and response ownership."
          />
          <ActionCard
            href="/exports"
            title="Export evidence"
            helper="Download evidence-ready records for audit, review, or customer reporting."
          />
          <ActionCard
            href="/monitoring-sources"
            title="Configure response policy"
            helper="Define escalation and response handling rules."
          />
        </div>
      )}
    </article>
  );
}
