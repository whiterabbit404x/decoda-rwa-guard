import { getRuntimeConfig } from '../../runtime-config';

export const dynamic = 'force-dynamic';

export async function GET(): Promise<Response> {
  const runtimeConfig = getRuntimeConfig();

  return Response.json(runtimeConfig, {
    headers: {
      'Cache-Control': 'no-store',
    },
  });
}
