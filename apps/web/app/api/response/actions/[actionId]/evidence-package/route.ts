import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

export async function POST(
  request: Request,
  { params }: { params: Promise<{ actionId: string }> },
): Promise<Response> {
  const { actionId } = await params;
  return proxyJsonToBackend(request, {
    backendPath: `/response/actions/${actionId}/evidence-package`,
    method: 'POST',
  });
}
