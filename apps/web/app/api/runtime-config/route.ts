import { browserApiUrl } from '../../api-config';
import { getRuntimeConfig } from '../../runtime-config';

export const dynamic = 'force-dynamic';

export async function GET(): Promise<Response> {
  const serverConfig = getRuntimeConfig();
  // The browser gets the same-origin proxy as its API base, never the API itself.
  const runtimeConfig = { ...serverConfig, apiUrl: browserApiUrl(serverConfig.apiUrl) };

  return Response.json(runtimeConfig, {
    headers: {
      'Cache-Control': 'no-store',
    },
  });
}
