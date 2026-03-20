import {
  fetchDashboardPageData,
  resolveGatewayReachability,
} from '../../dashboard-data';

export const dynamic = 'force-dynamic';

export async function GET(request: Request): Promise<Response> {
  const { searchParams } = new URL(request.url);
  const apiUrl = searchParams.get('apiUrl')?.trim();
  const data = await fetchDashboardPageData(apiUrl || undefined);

  const meta = {
    gatewayReachable: resolveGatewayReachability(data.dashboard),
    dashboardFetchSucceeded: data.dashboard !== null,
    riskLive: data.riskDashboard.source === 'live',
    threatLive: data.threatDashboard.source === 'live',
    complianceLive: data.complianceDashboard.source === 'live',
    resilienceLive: data.resilienceDashboard.source === 'live',
    diagnostics: data.diagnostics,
    experienceState: data.diagnostics.experienceState,
    sampleMode: data.diagnostics.sampleMode,
    errors: data.diagnostics.degradedReasons,
  };

  return Response.json(
    {
      data,
      meta,
    },
    {
      headers: {
        'Cache-Control': 'no-store',
      },
    }
  );
}
