import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Same-origin proxy for POST /run-detection (Alerts page "Run Detection" empty-state CTA).
export async function POST(request: Request): Promise<Response> {
  return proxyJsonToBackend(request, {
    backendPath: '/run-detection',
    method: 'POST',
    forwardBody: true,
  });
}
