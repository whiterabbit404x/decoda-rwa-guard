import { fetchDashboardPageData } from '../../dashboard-data';

export const dynamic = 'force-dynamic';

export async function GET(): Promise<Response> {
  const data = await fetchDashboardPageData();

  return Response.json(data, {
    headers: {
      'Cache-Control': 'no-store',
    },
  });
}
