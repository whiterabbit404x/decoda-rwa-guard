import { fetchDashboardPageData } from '../../dashboard-data';

export const dynamic = 'force-dynamic';

async function getAlerts(apiUrl: string) {
  const response = await fetch(`${apiUrl}/alerts`, { cache: 'no-store' });
  if (!response.ok) return [];
  const payload = await response.json();
  return payload.alerts ?? [];
}

export default async function AlertsPage() {
  const data = await fetchDashboardPageData();
  const alerts = await getAlerts(data.apiUrl);
  return <main className="productPage"><section className="dataCard"><h1>Alerts</h1>{alerts.length === 0 ? <p className="muted">No alerts yet. Configure modules and run monitoring to generate alerts.</p> : alerts.map((a: any) => <p key={a.id}>{a.title} · {a.severity} · {a.status}</p>)}</section></main>;
}
