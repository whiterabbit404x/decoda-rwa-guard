import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Same-origin proxy for the entire /alerts backend surface so the Alerts page never calls
// the backend directly (which silently fails in production when NEXT_PUBLIC_API_URL is unset).
// Covers: GET /alerts (list + count cards), POST /alerts/open-from-detection (Open Alert),
// POST /alerts/{id}/escalate, GET /alerts/{id}/evidence, GET/PATCH /alerts/{id}.
function backendPath(path?: string[]): string {
  if (!path || path.length === 0) {
    return '/alerts';
  }
  return `/alerts/${path.map((segment) => encodeURIComponent(segment)).join('/')}`;
}

export async function GET(
  request: Request,
  { params }: { params: Promise<{ path?: string[] }> },
): Promise<Response> {
  const { path } = await params;
  const url = new URL(request.url);
  return proxyJsonToBackend(request, {
    backendPath: backendPath(path),
    method: 'GET',
    searchParams: url.searchParams,
  });
}

export async function POST(
  request: Request,
  { params }: { params: Promise<{ path?: string[] }> },
): Promise<Response> {
  const { path } = await params;
  return proxyJsonToBackend(request, {
    backendPath: backendPath(path),
    method: 'POST',
    forwardBody: true,
  });
}

export async function PATCH(
  request: Request,
  { params }: { params: Promise<{ path?: string[] }> },
): Promise<Response> {
  const { path } = await params;
  return proxyJsonToBackend(request, {
    backendPath: backendPath(path),
    method: 'PATCH',
    forwardBody: true,
  });
}
