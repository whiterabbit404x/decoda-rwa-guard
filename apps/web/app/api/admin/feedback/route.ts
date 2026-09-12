import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

export async function GET(request: Request): Promise<Response> {
  const url = new URL(request.url);
  const searchParams = new URLSearchParams();
  // Allowlisted, not forwarded wholesale: the backend authorizes internal admin
  // before reading anything, and a narrow list keeps this proxy from becoming a
  // way to reach query parameters the endpoint never meant to expose.
  for (const key of [
    'organization_id',
    'limit',
    'feedback_type',
    'severity',
    'production_blocker',
    'feedback_mode',
    'since',
    'until',
  ]) {
    const value = url.searchParams.get(key);
    if (value) {
      searchParams.set(key, value);
    }
  }
  return proxyJsonToBackend(request, { backendPath: '/admin/feedback', method: 'GET', searchParams });
}
