import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Same-origin proxy for the Autonomous Onboarding Agent. The browser must NEVER call the
// backend API URL directly: in production that origin is not browser-reachable (internal
// Railway URL / cross-origin / http), which surfaces in the UI as a raw "Failed to fetch".
// Every onboarding request is forwarded server-side through the runtime-resolved backend
// URL, mirroring the alerts / incidents / targets transport. Backend onboarding routes are
// registered under the /api/onboarding/* prefix, so the forwarded path keeps that prefix.
//
// POST /api/onboarding/sessions — create or resume an onboarding session.
export async function POST(request: Request): Promise<Response> {
  return proxyJsonToBackend(request, {
    backendPath: '/api/onboarding/sessions',
    method: 'POST',
    forwardBody: true,
  });
}
