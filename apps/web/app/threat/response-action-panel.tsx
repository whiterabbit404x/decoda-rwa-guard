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

export default function ResponseActionPanel({ capabilities, actions, loading = false }: Props) {
  const capabilityLabels = capabilities.length > 0 ? capabilities : ['Simulation only'];

  return (
    <article className="dataCard" aria-label="Response Actions" id="response-actions">
      <p className="sectionEyebrow">Response actions</p>
      <h3>Operational actions</h3>
      <div className="chipRow">
        {capabilityLabels.map((label) => (
          <span className="ruleChip" key={label}>{label}</span>
        ))}
      </div>
      <div className="stack" role="status" aria-live="polite">
        {loading ? <p>Loading response actions…</p> : null}
        {!loading && actions.length === 0 ? <p>No response actions available.</p> : null}
        {actions.map((action) => (
          <div className="stack stackCompact" key={action.id}>
            <p className="mutedText">{RESPONSE_ACTION_STATE_LABELS[action.state]}</p>
            <button type="button" disabled={action.disabled} title={action.reason} onClick={action.onClick}>{action.label}</button>
          </div>
        ))}
      </div>
      <div className="buttonRow"><Link href="/alerts" prefetch={false}>Review alerts</Link><Link href="/incidents" prefetch={false}>Open incident queue</Link></div>
    </article>
  );
}
