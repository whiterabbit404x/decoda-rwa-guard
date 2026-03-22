import type { BuildInfo } from './build-info';

type AuthDeploymentBadgeProps = {
  buildInfo: BuildInfo;
  compact?: boolean;
};

function envValue(value: string | null | undefined, fallback = 'unknown') {
  const normalized = value?.trim();
  return normalized ? normalized : fallback;
}

function badgeItems(buildInfo: BuildInfo) {
  return [
    { label: 'Environment', value: envValue(buildInfo.vercelEnv) },
    { label: 'Commit', value: envValue(buildInfo.gitCommitShaShort) },
    { label: 'Branch', value: envValue(buildInfo.gitBranch) },
    { label: 'Host', value: envValue(buildInfo.currentHost ?? buildInfo.vercelUrl) },
    { label: 'Auth mode', value: envValue(buildInfo.authMode) },
  ];
}

export function formatBuildVersionLine(buildInfo: BuildInfo) {
  return `Build: ${envValue(buildInfo.gitCommitShaShort)} · ${envValue(buildInfo.gitBranch)} · ${envValue(buildInfo.vercelEnv)}`;
}

export default function AuthDeploymentBadge({ buildInfo, compact = false }: AuthDeploymentBadgeProps) {
  const items = badgeItems(buildInfo);

  if (compact) {
    return (
      <div className="authDeploymentBadge authDeploymentBadgeCompact" aria-label="deployment badge compact">
        {items.map((item) => (
          <span key={item.label}>
            <strong>{item.label}:</strong> {item.value}
          </span>
        ))}
      </div>
    );
  }

  return (
    <aside className="dataCard authDeploymentBadge" role="status" aria-live="polite">
      <p className="sectionEyebrow">Running deployment</p>
      <h2>Deployment identity</h2>
      <p className="muted">Use this badge to verify the exact deployment before debugging auth behavior on preview or production URLs.</p>
      <div className="kvGrid compactKvGrid authDeploymentGrid">
        {items.map((item) => (
          <p key={item.label}>
            <span>{item.label}</span>
            {item.value}
          </p>
        ))}
      </div>
    </aside>
  );
}
