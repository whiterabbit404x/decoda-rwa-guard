import { resolveApiUrl } from '../../dashboard-data';
import IncidentsPageClient from '../incidents-page-client';
import RuntimeSummaryPanel from '../../runtime-summary-panel';

export const dynamic = 'force-dynamic';

export default async function IncidentsPage() {
  return <IncidentsPageClient apiUrl={resolveApiUrl()} />;
}
