import type { BuildInfo } from './build-info';
import type { RuntimeConfig } from './runtime-config-schema';
import AuthDeploymentBadge from './auth-deployment-badge';
import AuthDiagnosticCard from './auth-diagnostic-card';
import PreviewDeploymentNotice from './preview-deployment-notice';

type AuthRuntimePanelProps = {
  buildInfo: BuildInfo;
  runtimeConfig: RuntimeConfig;
  loading?: boolean;
};

export default function AuthRuntimePanel({ buildInfo, runtimeConfig, loading = false }: AuthRuntimePanelProps) {
  return (
    <div className="authRuntimePanel">
      <AuthDeploymentBadge buildInfo={buildInfo} />
      {buildInfo.vercelEnv === 'preview' ? <PreviewDeploymentNotice buildInfo={buildInfo} /> : null}
      <AuthDiagnosticCard loading={loading} runtimeConfig={runtimeConfig} buildInfo={buildInfo} />
      <AuthDeploymentBadge buildInfo={buildInfo} compact />
    </div>
  );
}
