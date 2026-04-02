import { resolveApiUrl } from '../../dashboard-data';
import AlertsPageClient from '../alerts-page-client';

export const dynamic = 'force-dynamic';

export default async function AlertsPage() {
  return <AlertsPageClient apiUrl={resolveApiUrl()} />;
}
