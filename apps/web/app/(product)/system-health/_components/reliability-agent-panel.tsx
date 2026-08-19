import { type ReliabilityOverview } from './reliability-types';
import { ReliabilityAgentControls } from './reliability-agent-controls';

type Props = {
  overview: ReliabilityOverview;
};

function iconFor(state: ReliabilityOverview['diagnosis']['state']): { cls: string; glyph: string } {
  if (state === 'nominal') return { cls: 'healthIcon healthIconLive', glyph: '✓' };
  if (state === 'action_required') return { cls: 'healthIcon healthIconOffline', glyph: '✕' };
  return { cls: 'healthIcon healthIconDegraded', glyph: '!' };
}

function autoHealingLabel(mode: string): string {
  if (mode === 'enabled') return 'Auto-Healing: Enabled';
  if (mode === 'disabled') return 'Auto-Healing: Disabled';
  return 'Auto-Healing: Recommendation only';
}

export function ReliabilityAgentPanel({ overview }: Props) {
  const { diagnosis } = overview;
  const icon = iconFor(diagnosis.state);
  const root = diagnosis.root_cause;

  return (
    <section className="dataCard shRelAgentPanel">
      <div className="sectionHeader compact">
        <div>
          <p className="sectionEyebrow">AI operational diagnosis</p>
          <h2>Self-Healing Reliability Agent</h2>
        </div>
      </div>

      <div className="shRelAgentHero">
        <div className={icon.cls} aria-hidden="true">
          {icon.glyph}
        </div>
        <div>
          <p className="shRelAgentHeadline">{diagnosis.headline}</p>
          <p className="shRelAgentSub">{autoHealingLabel(diagnosis.auto_healing_mode)}</p>
        </div>
      </div>

      {/* Observations — evidence-based, deterministic. */}
      <div className="shRelAgentBlock">
        <p className="shRelAgentBlockTitle">Observations</p>
        <ul className="shRelAgentList">
          {diagnosis.observations.map((line, i) => (
            <li key={i}>{line}</li>
          ))}
        </ul>
      </div>

      {/* Root-cause assessment — only meaningful when there is a failure domain. */}
      {root.domain ? (
        <div className="shRelAgentBlock">
          <p className="shRelAgentBlockTitle">Probable root cause</p>
          <p className="shRelAgentRoot">
            <strong>{root.domain_label ?? root.domain}</strong> — {root.summary}
          </p>
          {root.symptoms.length > 0 ? (
            <p className="shSummaryTime">Downstream symptoms: {root.symptoms.join(', ')}</p>
          ) : null}
          <p className="shSummaryTime">Confidence: {root.confidence}</p>
        </div>
      ) : null}

      {/* Interactive controls (client): findings + remediation + full report.
          The frontend never authorizes a restart — every action re-checks
          policy and permissions server-side. */}
      <ReliabilityAgentControls overview={overview} />
    </section>
  );
}
