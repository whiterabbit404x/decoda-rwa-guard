import { type ReliabilityFetchResult } from './fetch-reliability-overview';
import { ReliabilitySummaryCards } from './reliability-summary-cards';
import { SystemComponentsTable } from './system-components-table';
import { ReliabilityAgentPanel } from './reliability-agent-panel';
import { AutonomousStatusStrip } from './autonomous-status-strip';

type Props = {
  result: ReliabilityFetchResult;
};

// Top-of-page Screen 12 section: summary cards, a two-column
// (System Components | Self-Healing Reliability Agent), and the autonomous
// status strip — matching the product's intended layout. If the reliability
// endpoint is unreachable we render an explicit unavailable panel rather than
// pretending the platform is healthy (a degraded page is still usable — the
// detailed system-health panels below render independently).
export function ReliabilityAgentSection({ result }: Props) {
  if (!result.ok) {
    return (
      <section className="dataCard" style={{ marginTop: '1rem' }}>
        <div className="sectionHeader compact">
          <div>
            <p className="sectionEyebrow">Self-Healing Reliability Agent</p>
            <h2>Reliability agent unavailable</h2>
          </div>
        </div>
        <p className="shSummarySignal">
          The reliability agent could not be reached, so its diagnosis and metrics are not shown. This does not mean the
          platform is healthy — it means this signal is currently unobservable.
        </p>
        <p className="shSummaryTime">{result.error}</p>
      </section>
    );
  }

  const overview = result.data;
  return (
    <div className="shRelSection">
      <ReliabilitySummaryCards metrics={overview.metrics} />

      <div className="shRelTwoColumn">
        <SystemComponentsTable components={overview.components} />
        <ReliabilityAgentPanel overview={overview} />
      </div>

      <AutonomousStatusStrip autonomous={overview.autonomous} />
    </div>
  );
}
