import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

type Params = { params: Promise<{ producer: string }> };

const PRODUCERS = new Set(['decoda-assets', 'decoda-vault']);

function unknownProducer(): Response {
  return Response.json({ detail: 'Unknown Decoda product.' }, { status: 404, headers: { 'Cache-Control': 'no-store' } });
}

// Route a Decoda product's events to the current workspace (organization admins; the backend enforces it).
export async function POST(request: Request, { params }: Params): Promise<Response> {
  const { producer } = await params;
  if (!PRODUCERS.has(producer)) return unknownProducer();
  return proxyJsonToBackend(request, { backendPath: `/integrations/decoda/links/${producer}`, method: 'POST' });
}

export async function DELETE(request: Request, { params }: Params): Promise<Response> {
  const { producer } = await params;
  if (!PRODUCERS.has(producer)) return unknownProducer();
  return proxyJsonToBackend(request, { backendPath: `/integrations/decoda/links/${producer}`, method: 'DELETE' });
}
