import { fetchDashboardPageData } from '../../dashboard-data';

export const dynamic = 'force-dynamic';

export default async function IntegrationsPage() {
  const data = await fetchDashboardPageData();
  const response = await fetch(`${data.apiUrl}/integrations/webhooks`, { cache: 'no-store' });
  const payload = response.ok ? await response.json() : { webhooks: [] };
  return <main className="productPage"><section className="dataCard"><h1>Integrations</h1><p className="muted">Signed outbound webhooks and delivery logs.</p>{(payload.webhooks ?? []).map((w: any) => <p key={w.id}>{w.target_url} · {w.enabled ? 'enabled' : 'disabled'}</p>)}</section></main>;
}
