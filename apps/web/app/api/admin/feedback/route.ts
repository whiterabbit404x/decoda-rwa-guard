import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

export async function GET(request: Request): Promise<Response> {
  const url = new URL(request.url);
  const searchParams = new URLSearchParams();
  for (const key of ['organization_id', 'limit']) {
    const value = url.searchParams.get(key);
    if (value) {
      searchParams.set(key, value);
    }
  }
  return proxyJsonToBackend(request, { backendPath: '/admin/feedback', method: 'GET', searchParams });
}
