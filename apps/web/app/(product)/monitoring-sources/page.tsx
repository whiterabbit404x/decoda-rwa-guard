import RuntimeSummaryPanel from '../../runtime-summary-panel';
import { EmptyStateBlocker } from '../../components/ui-primitives';

const TABS = [
  { key: 'targets', title: 'Targets', href: '/monitoring-sources/targets', detail: 'Define what activity should be monitored for each protected asset.' },
  { key: 'systems', title: 'Systems', href: '/monitoring-sources/monitored-systems', detail: 'Review and repair monitored systems generated from enabled targets.' },
];

export default function MonitoringSourcesPage() {
  return (
    <main className="productPage">
      <RuntimeSummaryPanel />
      <section className="dataCard stack">
        <h1>Monitoring Sources</h1>
        <p className="muted">Use the tabs below to manage targets and monitored systems.</p>
        <div className="buttonRow">
          {TABS.map((tab) => <a key={tab.key} href={tab.href}>{tab.title}</a>)}
        </div>
        <div className="threeColumnSection">
          <EmptyStateBlocker title="No targets in this view" body="Add or enable monitoring targets to begin live telemetry coverage." ctaHref="/monitoring-sources/targets" ctaLabel="Open targets" />
          <EmptyStateBlocker title="No monitored systems in this view" body="Systems appear after at least one target is enabled and synced." ctaHref="/monitoring-sources/monitored-systems" ctaLabel="Open monitored systems" />
        </div>
      </section>
    </main>
  );
}
