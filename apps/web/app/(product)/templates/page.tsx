import { fetchDashboardPageData } from '../../dashboard-data';

export const dynamic = 'force-dynamic';

export default async function TemplatesPage() {
  const data = await fetchDashboardPageData();
  const response = await fetch(`${data.apiUrl}/templates`, { cache: 'no-store' });
  const payload = response.ok ? await response.json() : { templates: [] };
  return <main className="productPage"><section className="dataCard"><h1>Templates</h1><p className="muted">Optional onboarding helpers. Templates do not replace live operations.</p>{(payload.templates ?? []).map((t: any) => <p key={t.id}>{t.name} · {t.module}</p>)}</section></main>;
}
