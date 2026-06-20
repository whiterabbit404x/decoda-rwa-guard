import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

export async function POST(
  request: Request,
  { params }: { params: { actionId: string } },
): Promise<Response> {
  return proxyJsonToBackend(request, {
    backendPath: `/response/actions/${params.actionId}/simulate`,
    method: 'POST',
  });
}
