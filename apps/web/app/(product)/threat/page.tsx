import ThreatDemoPanel from '../../threat-demo-panel';
import { fetchDashboardPageData, formatRules } from '../../dashboard-data';
import StatusBadge from '../../status-badge';
import SystemStatusPanel from '../../system-status-panel';

export const dynamic = 'force-dynamic';

export default async function ThreatPage() {
  const data = await fetchDashboardPageData();
  const { threatDashboard } = data;

  return (
    <main className="productPage">
      <section className="hero compactHero">
        <div>
          <p className="eyebrow">Threat operations</p>
          <h1>Preemptive exploit and anomaly defense</h1>
          <p className="lede">Monitor contract behavior, transaction anomalies, and treasury-token market risk without losing visibility when the threat engine degrades.</p>
        </div>
        <div className="heroPanel"><StatusBadge state={threatDashboard.source === 'live' && !threatDashboard.degraded ? 'live' : 'fallback'} /><p>{threatDashboard.message}</p></div>
      </section>
      <SystemStatusPanel diagnostics={data.diagnostics} dashboard={data.dashboard} />
      <section className="threeColumnSection">
        <div className="stack compactStack">
          {threatDashboard.active_alerts.map((alert) => (
            <article key={alert.id} className="dataCard">
              <div className="listHeader"><div><h3>{alert.title}</h3><p className="muted">{alert.category}</p></div><StatusBadge state={alert.source === 'live' ? 'live' : 'fallback'} compact /></div>
              <p className="explanation small">{alert.explanation}</p>
              <div className="chipRow">{formatRules(alert.patterns).map((pattern) => <span key={pattern} className="ruleChip">{pattern}</span>)}</div>
            </article>
          ))}
        </div>
        <ThreatDemoPanel apiUrl={data.apiUrl} />
        <div className="stack compactStack">
          {threatDashboard.recent_detections.map((detection) => (
            <article key={detection.id} className="dataCard">
              <div className="listHeader"><div><h3>{detection.title}</h3><p className="muted">{detection.category}</p></div><span className="severityPill high">{detection.action} · {detection.score}</span></div>
              <p className="explanation small">{detection.explanation}</p>
            </article>
          ))}
        </div>
      </section>
    </main>
  );
}
