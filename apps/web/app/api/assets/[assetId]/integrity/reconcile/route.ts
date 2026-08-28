import { proxyIntegrity } from '../_shared';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

/** Explicit operator diagnostic. RBAC and idempotency are enforced by the backend. */
export async function POST(request: Request, { params }: { params: Promise<{ assetId: string }> }): Promise<Response> {
  const { assetId } = await params;
  return proxyIntegrity(request, assetId, '/reconcile', 'POST', 'Timed out waiting for backend reconciliation.');
}
