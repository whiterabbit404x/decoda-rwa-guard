import { proxyIntegrity } from './_shared';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

/** Read-only asset integrity state. No POST handler exists here: a page load or
 *  refresh must never be able to create a reconciliation snapshot. */
export async function GET(request: Request, { params }: { params: Promise<{ assetId: string }> }): Promise<Response> {
  const { assetId } = await params;
  return proxyIntegrity(request, assetId, '', 'GET', 'Timed out waiting for backend asset integrity state.');
}
