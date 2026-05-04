import { resolveApiUrl } from '../../dashboard-data';
import AlertsPageClient from '../alerts-page-client';
import RuntimeSummaryPanel from '../../runtime-summary-panel';

export const dynamic = 'force-dynamic';

export default async function AlertsPage() {
  return <AlertsPageClient apiUrl={resolveApiUrl()} />;
}
