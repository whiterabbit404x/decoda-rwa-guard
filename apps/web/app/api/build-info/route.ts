import { getBuildInfo } from '../../build-info';

export const dynamic = 'force-dynamic';

export async function GET(): Promise<Response> {
  const buildInfo = getBuildInfo();

  return Response.json({
    vercelEnv: buildInfo.vercelEnv,
    vercelUrl: buildInfo.vercelUrl,
    gitCommitShaShort: buildInfo.gitCommitShaShort,
    gitBranch: buildInfo.gitBranch,
    nodeEnv: buildInfo.nodeEnv,
    buildTimestamp: buildInfo.buildTimestamp,
    authMode: buildInfo.authMode,
    runtimeConfigSummary: buildInfo.runtimeConfigSummary,
  }, {
    headers: {
      'Cache-Control': 'no-store',
    },
  });
}
