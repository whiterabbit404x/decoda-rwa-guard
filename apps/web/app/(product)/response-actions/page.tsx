import Link from 'next/link';
import RuntimeSummaryPanel from '../../runtime-summary-panel';
import { MetricTile, StatusPill, TableShell } from '../../components/ui-primitives';

const responseActionRows = [
  {
    action: 'Pause bridge transfers',
    type: 'Containment',
    impact: 'Stops high-risk movement while preserving audit evidence.',
    status: 'Recommended',
    recommendedBy: 'Policy engine',
    linkedIncident: 'INC-24017',
    evidenceSource: 'Realtime monitoring package',
    requiresApproval: 'Yes',
  },
  {
    action: 'Rotate compromised API key',
    type: 'Credential hardening',
    impact: 'Revokes compromised access and limits lateral movement.',
    status: 'Executed',
    recommendedBy: 'Analyst override',
    linkedIncident: 'INC-24015',
    evidenceSource: 'Credential telemetry package',
    requiresApproval: 'No',
  },
];

const componentRows = [
  { component: 'Worker fleet', status: 'Healthy', detail: 'All response workers acknowledge policy updates.' },
  { component: 'Queue', status: 'Healthy', detail: 'No backlog beyond autoscaling threshold.' },
  { component: 'Database', status: 'Healthy', detail: 'Replica lag remains inside 200ms target.' },
  { component: 'Provider health', status: 'Degraded', detail: 'Primary sanctions provider unavailable; fallback feed active.' },
];

export default function ResponseActionsPage() {
  return (
    <main className="productPage">
      <RuntimeSummaryPanel />

      <section className="featureSection">
        <div className="sectionHeader">
          <div>
            <h1>Response Actions</h1>
            <p className="muted">Operate from one command view with explicit recommended actions, historical execution, and simulator versus live labels.</p>
          </div>
        </div>

        <div className="buttonRow" role="tablist" aria-label="Response action views">
          <button type="button" role="tab" aria-selected className="activeTab">Recommended Actions</button>
          <button type="button" role="tab" aria-selected={false}>Action History</button>
        </div>

        <TableShell
          headers={[
            'Action',
            'Type',
            'Impact',
            'Status',
            'Recommended by',
            'Linked incident',
            'Evidence source',
            'Requires approval',
          ]}
        >
          {responseActionRows.map((row) => (
            <tr key={`${row.action}-${row.linkedIncident}`}>
              <td>{row.action}</td>
              <td>{row.type}</td>
              <td>{row.impact}</td>
              <td>{row.status}</td>
              <td>{row.recommendedBy}</td>
              <td><Link href="/incidents" prefetch={false}>{row.linkedIncident}</Link></td>
              <td>{row.evidenceSource}</td>
              <td>{row.requiresApproval}</td>
            </tr>
          ))}
        </TableShell>

        <div className="threeColumnSection">
          <article className="dataCard">
            <div className="listHeader">
              <h3>Execution labels</h3>
              <StatusPill label="Simulator" />
            </div>
            <p className="muted">Simulator actions are explicitly tagged as recommendations and never apply controls to production systems.</p>
            <div className="listHeader">
              <h3>Live execution</h3>
              <StatusPill label="Live" />
            </div>
            <p className="muted">Live actions are only shown after execution and include approver + timestamp in incident history.</p>
          </article>

          <article className="dataCard">
            <h3>Evidence package</h3>
            <p className="muted">Package records maintain incident linkage and include list for each response action decision.</p>
            <ul>
              <li>Incident linkage: required before package finalization.</li>
              <li>Includes list: alerts, timelines, payload snapshots, and operator notes.</li>
              <li>Export/download: enabled only when a package exists.</li>
            </ul>
          </article>
        </div>
      </section>

      <section className="featureSection">
        <div className="sectionHeader">
          <div>
            <h2>System Health</h2>
            <p className="muted">Track service availability and execution readiness before dispatching live actions.</p>
          </div>
        </div>

        <div className="metricsGrid twoColumnMetrics">
          <MetricTile label="Uptime" value="99.98%" meta="30 day rolling" />
          <MetricTile label="Latency" value="182ms" meta="p95 action dispatch" />
          <MetricTile label="Error rate" value="0.14%" meta="last 24h" />
          <MetricTile label="Active systems" value="42" meta="workers + integrations" />
          <MetricTile label="Worker" value="Healthy" meta="12/12 online" />
          <MetricTile label="Queue" value="Healthy" meta="17 pending jobs" />
          <MetricTile label="DB" value="Healthy" meta="Replica lag 94ms" />
          <MetricTile label="Provider health" value="Degraded" meta="Fallback source in use" />
          <MetricTile label="Last check" value="just now" meta="Auto refresh every 30s" />
        </div>

        <TableShell headers={['Component', 'Status', 'Degraded reason']}>
          {componentRows.map((row) => (
            <tr key={row.component}>
              <td>{row.component}</td>
              <td>{row.status}</td>
              <td>{row.status === 'Degraded' ? row.detail : 'N/A'}</td>
            </tr>
          ))}
        </TableShell>
      </section>
    </main>
  );
}
