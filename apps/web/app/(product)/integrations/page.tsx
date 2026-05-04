import { resolveApiUrl } from '../../dashboard-data';
import IntegrationsPageClient from '../integrations-page-client';
import RuntimeSummaryPanel from '../../runtime-summary-panel';

export const dynamic = 'force-dynamic';

export default async function IntegrationsPage() {
  return <IntegrationsPageClient apiUrl={resolveApiUrl()} />;
}
