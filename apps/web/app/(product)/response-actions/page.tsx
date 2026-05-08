import ResponseActionsPageClient from '../response-actions-page-client';
import { resolveApiUrl } from '../../dashboard-data';

export const dynamic = 'force-dynamic';

export default function ResponseActionsPage() {
  return <ResponseActionsPageClient apiUrl={resolveApiUrl()} />;
}
