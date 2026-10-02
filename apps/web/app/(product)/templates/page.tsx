import { resolveApiUrl } from '../../dashboard-data';
import { browserApiUrl } from 'app/api-config';
import TemplatesPageClient from '../templates-page-client';

export const dynamic = 'force-dynamic';

export default async function TemplatesPage() {
  return <TemplatesPageClient apiUrl={browserApiUrl(resolveApiUrl()) ?? ''} />;
}
