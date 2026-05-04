import Link from 'next/link';
import RuntimeSummaryPanel from '../../runtime-summary-panel';
import { ActionPanel, StatusPill } from '../../components/ui-primitives';

export default function ResponseActionsPage() {
  return (
    <main className="productPage">
      <RuntimeSummaryPanel />
      <section className="featureSection">
        <div className="sectionHeader"><div><h1>Response Actions</h1><p className="muted">Track recommended versus history, and clearly label simulated versus executed actions.</p></div></div>
        <div className="threeColumnSection">
          <ActionPanel title="Recommended actions">
            <StatusPill label="Simulator mode" />
            <p className="muted">Use simulator recommendations before live execution.</p>
            <p><Link href="/alerts" prefetch={false}>Review alerts</Link></p>
          </ActionPanel>
          <ActionPanel title="Execution history">
            <StatusPill label="Executed" />
            <p className="muted">Executed actions are attached to incident evidence and timeline context.</p>
            <p><Link href="/incidents" prefetch={false}>Open incidents</Link></p>
          </ActionPanel>
        </div>
      </section>
    </main>
  );
}
