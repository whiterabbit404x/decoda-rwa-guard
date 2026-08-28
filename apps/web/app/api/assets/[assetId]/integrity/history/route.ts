import { proxyIntegrity } from '../_shared';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

export async function GET(request: Request, { params }: { params: Promise<{ assetId: string }> }): Promise<Response> {
  const { assetId } = await params;
  return proxyIntegrity(request, assetId, '/history', 'GET', 'Timed out waiting for backend reconciliation history.');
}
