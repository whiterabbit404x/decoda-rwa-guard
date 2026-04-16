import {
  buildThreatDashboardRuntimeDiagnostics,
  fetchDashboardPageData,
  resolveGatewayReachability,
} from '../../dashboard-data';
import { normalizeMonitoringPresentation } from '../../monitoring-status-presentation';
import { resolveWorkspaceMonitoringTruthFromSummary } from '../../workspace-monitoring-truth';

export const dynamic = 'force-dynamic';

export async function GET(request: Request): Promise<Response> {
  const { searchParams } = new URL(request.url);
  const apiUrl = searchParams.get('apiUrl')?.trim();
  const requestSource = searchParams.get('source')?.trim() || 'hydrator';
  console.info(`[dashboard-page-data trace] source=${requestSource} path=/api/dashboard-page-data`);
  const data = await fetchDashboardPageData(apiUrl || undefined, { requestSource });
  const monitoringTruth = resolveWorkspaceMonitoringTruthFromSummary(data.workspaceMonitoringSummary);
  const monitoringPresentation = normalizeMonitoringPresentation(monitoringTruth);

  const meta = {
    gatewayReachable: resolveGatewayReachability(data.dashboard),
    dashboardFetchSucceeded: data.dashboard !== null,
    riskLive: data.riskDashboard.source === 'live' && !data.riskDashboard.degraded,
    threatLive: data.threatDashboard.source === 'live' && !data.threatDashboard.degraded,
    complianceLive: data.complianceDashboard.source === 'live' && !data.complianceDashboard.degraded,
    resilienceLive: data.resilienceDashboard.source === 'live' && !data.resilienceDashboard.degraded,
    live: monitoringPresentation.status === 'live',
    diagnostics: data.diagnostics,
    threatDiagnostics: buildThreatDashboardRuntimeDiagnostics(data),
    experienceState: monitoringPresentation.status,
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
