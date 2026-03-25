import { dynamic, proxyAuthRequest, revalidate } from '../../_shared/proxy';

export { dynamic, revalidate };

export async function GET(request: Request) {
  return proxyAuthRequest(request, '/workspaces/members', 'GET', { requireAuth: true });
}
