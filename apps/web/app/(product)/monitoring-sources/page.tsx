import Link from 'next/link';
import RuntimeSummaryPanel from '../../runtime-summary-panel';

export default function MonitoringSourcesPage() {
  return (
    <main className="productPage">
      <RuntimeSummaryPanel />
      <section className="dataCard stack">
        <h1>Monitoring Sources</h1>
        <p className="muted">
          Manage the sources that power detection coverage by configuring monitoring targets and the linked monitored
          systems they generate.
        </p>
        <div className="stack compactStack">
          <article className="dataCard">
            <h2>Monitoring Targets</h2>
            <p className="muted">Define what activity should be monitored for each protected asset.</p>
            <Link href="/monitoring-sources/targets" prefetch={false}>Open targets</Link>
          </article>
          <article className="dataCard">
            <h2>Monitored Systems</h2>
            <p className="muted">Review and repair workspace systems generated from enabled monitoring targets.</p>
            <Link href="/monitoring-sources/monitored-systems" prefetch={false}>Open monitored systems</Link>
          </article>
        </div>
      </section>
    </main>
  );
}
