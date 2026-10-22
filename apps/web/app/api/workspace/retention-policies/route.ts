import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Same-origin proxy for the Settings "Data retention" card. The card has read
// GET /workspace/retention-policies since it shipped, but no route proxied it,
// so every load answered with the Next 404 page and the card could only ever
// report "unavailable". Read-only: the backend authorizes the caller and scopes
// the answer to the X-Workspace-Id workspace.
export async function GET(request: Request): Promise<Response> {
  return proxyJsonToBackend(request, { backendPath: '/workspace/retention-policies', method: 'GET' });
}
