import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

/**
 * Screen 8's policy-evaluation RECOVERY path, proxied same-origin.
 *
 * The backend runs the deterministic enforcement evaluation from canonical rows
 * and persists it with `simulation = FALSE`. Nothing is forwarded but the action
 * id in the path: the endpoint takes no request body, so the browser cannot
 * supply a decision, a policy version, an operator authority or an amount.
 */
export async function POST(
  request: Request,
  { params }: { params: Promise<{ actionId: string }> },
): Promise<Response> {
  const { actionId } = await params;
  return proxyJsonToBackend(request, {
    backendPath: `/response/actions/${encodeURIComponent(actionId)}/policy-evaluation`,
    method: 'POST',
  });
}
