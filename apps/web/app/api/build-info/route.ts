import { getBuildInfo } from '../../build-info';

export const dynamic = 'force-dynamic';

export async function GET(): Promise<Response> {
  return Response.json(getBuildInfo(), {
    headers: {
      'Cache-Control': 'no-store',
    },
  });
}
