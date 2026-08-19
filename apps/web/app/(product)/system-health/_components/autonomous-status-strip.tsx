import { type AutonomousStatus } from './reliability-types';

type Props = {
  autonomous: AutonomousStatus;
};

function autoHealingText(mode: string): string {
  if (mode === 'enabled') return 'Enabled';
  if (mode === 'disabled') return 'Disabled';
  return 'Recommendation only';
}

// A numeric field renders its value, or "—" when the ledger cannot be read
// (never a fabricated 0). A real 0 is distinct from unavailable.
function numeric(value: number | null | undefined): string {
  return value == null ? '—' : String(value);
}

export function AutonomousStatusStrip({ autonomous }: Props) {
  const last = autonomous.last_event;
  return (
    <section className="dataCard shRelStrip">
      <div className="sectionHeader compact">
        <div>
          <p className="sectionEyebrow">Autonomous infrastructure status</p>
          <h2>Self-Healing Activity</h2>
        </div>
      </div>
      <div className="shRelStripGrid">
        <div className="shRelStripCell">
          <p className="shRelStripLabel">Auto-Healing</p>
          <p className="shRelStripValue">{autoHealingText(autonomous.auto_healing)}</p>
        </div>
        <div className="shRelStripCell">
          <p className="shRelStripLabel">Events (24h)</p>
          <p className="shRelStripValue">{numeric(autonomous.events_24h)}</p>
        </div>
        <div className="shRelStripCell">
          <p className="shRelStripLabel">Restarts</p>
          <p className="shRelStripValue">{numeric(autonomous.restarts)}</p>
        </div>
        <div className="shRelStripCell">
          <p className="shRelStripLabel">Policies</p>
          <p className="shRelStripValue">{autonomous.policies}</p>
        </div>
        <div className="shRelStripCell shRelStripCellWide">
          <p className="shRelStripLabel">Last Event</p>
          {last && last.summary ? (
            <p className="shRelStripValue shRelStripLast" title={last.at ?? undefined}>
              {last.summary} <span className="shSummaryTime">{last.ago ?? ''}</span>
            </p>
          ) : (
            <p className="shRelStripValue">No remediation events recorded</p>
          )}
        </div>
      </div>
      {autonomous.auto_healing !== 'enabled' ? (
        <p className="explanation small" style={{ marginTop: '0.75rem' }}>
          Autonomous execution is {autonomous.auto_healing === 'disabled' ? 'disabled' : 'recommendation-only'}. The agent
          diagnoses and recommends; a supported action executes only via an authorized, policy-gated, verified control.
          {autonomous.configured_mechanisms.length === 0
            ? ' No automatic remediation mechanism is configured for this deployment.'
            : ` Configured mechanisms: ${autonomous.configured_mechanisms.join(', ')}.`}
        </p>
      ) : null}
    </section>
  );
}
