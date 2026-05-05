import { resolveApiUrl } from '../../dashboard-data';
import ResponseActionsPageClient from '../response-actions-page-client';

export const dynamic = 'force-dynamic';

export default async function ResponseActionsPage() {
  return <ResponseActionsPageClient apiUrl={resolveApiUrl()} />;
}
