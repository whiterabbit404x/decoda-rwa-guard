import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

type Params = { params: Promise<{ organizationId: string }> };

export async function GET(request: Request, { params }: Params): Promise<Response> {
  const { organizationId } = await params;
  return proxyJsonToBackend(request, {
    backendPath: `/admin/customers/${encodeURIComponent(organizationId)}`,
    method: 'GET',
  });
}
