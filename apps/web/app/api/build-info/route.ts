import { getBuildInfo } from '../../build-info';

export const dynamic = 'force-dynamic';

export async function GET(request: Request): Promise<Response> {
  return Response.json(getBuildInfo(process.env, { host: request.headers.get('x-forwarded-host') ?? request.headers.get('host') }), {
    headers: {
      'Cache-Control': 'no-store',
    },
  });
}
