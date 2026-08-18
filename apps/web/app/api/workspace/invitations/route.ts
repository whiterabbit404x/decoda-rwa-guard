import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Same-origin proxy for workspace invitations (Settings Team tab).
export async function GET(request: Request): Promise<Response> {
  return proxyJsonToBackend(request, { backendPath: '/workspace/invitations', method: 'GET' });
}

export async function POST(request: Request): Promise<Response> {
  return proxyJsonToBackend(request, { backendPath: '/workspace/invitations', method: 'POST', forwardBody: true });
}
