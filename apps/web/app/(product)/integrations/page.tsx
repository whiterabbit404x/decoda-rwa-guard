import { resolveApiUrl } from '../../dashboard-data';
import IntegrationsPageClient from '../integrations-page-client';

export const dynamic = 'force-dynamic';

export default async function IntegrationsPage() {
  return <IntegrationsPageClient apiUrl={resolveApiUrl()} />;
}
