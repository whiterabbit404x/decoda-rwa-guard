import { getBrowserRuntimeConfig } from '../../runtime-config';

export const dynamic = 'force-dynamic';

export async function GET(): Promise<Response> {
  // The browser gets the same-origin proxy as its API base, never the API itself.
  return Response.json(getBrowserRuntimeConfig(), {
    headers: {
      'Cache-Control': 'no-store',
    },
  });
}
