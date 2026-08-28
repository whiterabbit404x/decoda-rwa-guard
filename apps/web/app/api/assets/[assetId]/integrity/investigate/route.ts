import { proxyIntegrity } from '../_shared';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

/** Opens (or returns) the existing investigation. Never executes a response action. */
export async function POST(request: Request, { params }: { params: Promise<{ assetId: string }> }): Promise<Response> {
  const { assetId } = await params;
  return proxyIntegrity(request, assetId, '/investigate', 'POST', 'Timed out waiting for backend investigation.');
}
