import { resolveApiUrl } from '../../dashboard-data';
import IncidentsPageClient from '../incidents-page-client';

export const dynamic = 'force-dynamic';

export default async function IncidentsPage() {
  return <IncidentsPageClient apiUrl={resolveApiUrl()} />;
}
