import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Send failed signals to Decoda Assets / Decoda Vault again (workspace admins).
export async function POST(request: Request): Promise<Response> {
  return proxyJsonToBackend(request, { backendPath: '/integrations/decoda/signals/retry', method: 'POST' });
}
