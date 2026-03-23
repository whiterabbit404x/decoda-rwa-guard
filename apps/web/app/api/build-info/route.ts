import { getBuildInfo } from '../../build-info';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

export async function GET(request: Request): Promise<Response> {
  const currentHost = request.headers.get('x-forwarded-host') ?? request.headers.get('host');

  return Response.json(getBuildInfo(process.env, currentHost), {
    headers: {
      'Cache-Control': 'no-store, max-age=0',
    },
  });
}
