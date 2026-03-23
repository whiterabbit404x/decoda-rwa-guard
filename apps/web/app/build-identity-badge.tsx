import type { BuildInfo } from './build-info';

function displayValue(value: string | null | undefined, fallback = 'unknown') {
  return value && value.trim() ? value : fallback;
}

function formatBuildTimestamp(timestamp: string | null) {
  if (!timestamp) {
    return 'unavailable';
  }

  const parsedDate = new Date(timestamp);

  if (Number.isNaN(parsedDate.getTime())) {
    return timestamp;
  }

  return parsedDate.toISOString();
}

export function getBuildVersionLine(buildInfo: BuildInfo) {
  const shortCommitSha = buildInfo.commitSha ? buildInfo.commitSha.slice(0, 7) : 'unknown';

  return `Build: ${shortCommitSha} · ${displayValue(buildInfo.branch)} · ${displayValue(buildInfo.vercelEnv, 'unknown env')}`;
}

export default function BuildIdentityBadge({ buildInfo }: { buildInfo: BuildInfo }) {
  const shortCommitSha = buildInfo.commitSha ? buildInfo.commitSha.slice(0, 7) : null;

  return (
    <aside className="dataCard buildIdentityBadge" role="status" aria-live="polite">
      <div className="buildIdentityBadgeHeader">
        <div>
          <p className="sectionEyebrow">Deployment identity</p>
          <h2>Exact auth build</h2>
        </div>
        <p className="buildIdentityVersion">{getBuildVersionLine(buildInfo)}</p>
      </div>
      <p className="muted buildIdentitySummary">
        Use this badge to confirm you are on the expected deployment before debugging auth behavior.
      </p>
      <div className="kvGrid compactKvGrid buildIdentityGrid">
        <p>
          <span>environment</span>
          {displayValue(buildInfo.vercelEnv, 'development')}
        </p>
        <p>
          <span>current host</span>
          {displayValue(buildInfo.host)}
        </p>
        <p>
          <span>branch</span>
          {displayValue(buildInfo.branch)}
        </p>
        <p>
          <span>short commit SHA</span>
          {displayValue(shortCommitSha)}
        </p>
        <p>
          <span>build timestamp</span>
          {formatBuildTimestamp(buildInfo.buildTimestamp)}
        </p>
        <p>
          <span>auth mode</span>
          {buildInfo.authMode}
        </p>
      </div>
    </aside>
  );
}
