import ComplianceDemoPanel from '../../compliance-demo-panel';
import { fetchDashboardPageData, formatRules, statusTone } from '../../dashboard-data';
import StatusBadge from '../../status-badge';
import SystemStatusPanel from '../../system-status-panel';

export const dynamic = 'force-dynamic';

export default async function CompliancePage() {
  const data = await fetchDashboardPageData();
  const { complianceDashboard } = data;

  return (
    <main className="productPage">
      <section className="hero compactHero">
        <div>
          <p className="eyebrow">Compliance operations</p>
          <h1>Sovereign-grade policy and governance controls</h1>
          <p className="lede">Screen transfers, route jurisdictional policy decisions, and record governance actions with deterministic customer-ready explanations.</p>
        </div>
        <div className="heroPanel"><StatusBadge state={complianceDashboard.source === 'live' && !complianceDashboard.degraded ? 'live' : 'fallback'} /><p>{complianceDashboard.message}</p></div>
      </section>
      <SystemStatusPanel diagnostics={data.diagnostics} dashboard={data.dashboard} />
      <section className="threeColumnSection">
        <div className="stack compactStack">
          <article className="dataCard">
            <div className="listHeader"><div><h3>Transfer screening</h3><p className="muted">{complianceDashboard.transfer_screening.wrapper_status}</p></div><span className={`severityPill ${statusTone(complianceDashboard.transfer_screening.decision)}`}>{complianceDashboard.transfer_screening.decision}</span></div>
            <p className="explanation small">{complianceDashboard.transfer_screening.explainability_summary}</p>
            <div className="chipRow">{formatRules(complianceDashboard.transfer_screening.reasons).map((reason) => <span key={reason} className="ruleChip">{reason}</span>)}</div>
          </article>
          <article className="dataCard">
            <div className="listHeader"><div><h3>Residency screening</h3><p className="muted">{complianceDashboard.residency_screening.governance_status}</p></div><span className={`severityPill ${statusTone(complianceDashboard.residency_screening.residency_decision)}`}>{complianceDashboard.residency_screening.residency_decision}</span></div>
            <p className="explanation small">{complianceDashboard.residency_screening.explainability_summary}</p>
          </article>
        </div>
        <ComplianceDemoPanel apiUrl={data.apiUrl} />
        <div className="stack compactStack">
          {complianceDashboard.latest_governance_actions.map((action) => (
            <article key={action.action_id} className="dataCard">
              <div className="listHeader"><div><h3>{action.action_type}</h3><p className="muted">{action.target_type} · {action.target_id}</p></div><StatusBadge state={complianceDashboard.source === 'live' ? 'live' : 'fallback'} compact /></div>
              <p className="explanation small">{action.reason}</p>
            </article>
          ))}
        </div>
      </section>
    </main>
  );
}
