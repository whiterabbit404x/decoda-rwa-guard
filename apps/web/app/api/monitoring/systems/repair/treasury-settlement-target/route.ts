import { normalizeApiBaseUrl } from 'app/api-config';
import { getRuntimeConfig } from 'app/runtime-config';
import { normalizeWorkspaceHeaderValue } from 'app/workspace-header';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

export async function POST(request: Request) {
  const runtimeConfig = getRuntimeConfig();
  const backendApiUrl = normalizeApiBaseUrl(runtimeConfig.apiUrl);
  if (!runtimeConfig.configured || !backendApiUrl) {
    return Response.json({ detail: 'Runtime proxy is not configured.', code: 'invalid_runtime_config' }, { status: 500 });
  }

  const headers = new Headers();
  headers.set('Accept', 'application/json');
  headers.set('Content-Type', 'application/json');
  const authorization = request.headers.get('authorization');
  if (authorization) headers.set('Authorization', authorization);
  const workspaceId = normalizeWorkspaceHeaderValue(request.headers.get('x-workspace-id'));
  if (workspaceId) headers.set('X-Workspace-Id', workspaceId);

  const response = await fetch(`${backendApiUrl}/monitoring/systems/repair/treasury-settlement-target`, {
    method: 'POST',
    headers,
    cache: 'no-store',
  });
  const payload = await response.json().catch(() => ({}));
  return Response.json(payload, { status: response.status, headers: { 'Cache-Control': 'no-store' } });
}
