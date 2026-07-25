// Canonical, fail-closed presentation for the System Health "Deployment Version"
// section. Given the raw web (Vercel) / API (Railway) build facts, it produces the
// exact rows the UI renders. A missing value is NEVER fabricated: it renders the
// literal DEPLOYMENT_METADATA_UNAVAILABLE string so an operator can trust that a
// shown SHA is real and diagnose a stale deployment by comparing web vs API.

// The one truthful string shown for any missing field. Must never be a value that
// could be mistaken for a real SHA or timestamp.
export const DEPLOYMENT_METADATA_UNAVAILABLE = 'Unknown — build metadata unavailable';

// Short SHA length. Matches build-info.ts `shortCommitSha` (first 7 chars) so the
// visible text is the same short SHA operators already search for in logs.
const SHORT_SHA_LENGTH = 7;

export type DeploymentVersionInput = {
  webCommit?: string | null;
  apiCommit?: string | null;
  webBuildTime?: string | null;
  apiDeployTime?: string | null;
};

export type DeploymentVersionFieldKey =
  | 'web-commit'
  | 'api-commit'
  | 'web-build-time'
  | 'api-deploy-time';

export type DeploymentVersionField = {
  key: DeploymentVersionFieldKey;
  label: string;
  kind: 'commit' | 'timestamp';
  // Whether real metadata was available. When false the row still renders — it just
  // shows the truthful "unknown" message instead of being hidden.
  available: boolean;
  // The text shown in the DOM: the shortened SHA / timestamp when available, or the
  // truthful unavailable message when missing.
  display: string;
  // The full, untruncated value for the copy button / tooltip. null when missing so
  // the UI does not offer to copy a value that does not exist.
  fullValue: string | null;
};

function normalize(value: string | null | undefined): string | null {
  if (value == null) {
    return null;
  }

  const trimmed = String(value).trim();
  return trimmed.length > 0 ? trimmed : null;
}

// Shorten a commit SHA for display. Returns null (not '') for a missing SHA so the
// caller renders the truthful unavailable message.
export function shortenCommitSha(sha: string | null | undefined): string | null {
  const normalized = normalize(sha);
  if (!normalized) {
    return null;
  }

  return normalized.length > SHORT_SHA_LENGTH ? normalized.slice(0, SHORT_SHA_LENGTH) : normalized;
}

function commitField(
  key: DeploymentVersionFieldKey,
  label: string,
  rawSha: string | null | undefined,
): DeploymentVersionField {
  const full = normalize(rawSha);
  const short = shortenCommitSha(full);

  return {
    key,
    label,
    kind: 'commit',
    available: short != null,
    display: short ?? DEPLOYMENT_METADATA_UNAVAILABLE,
    fullValue: full,
  };
}

function timestampField(
  key: DeploymentVersionFieldKey,
  label: string,
  rawValue: string | null | undefined,
): DeploymentVersionField {
  const value = normalize(rawValue);

  return {
    key,
    label,
    kind: 'timestamp',
    available: value != null,
    display: value ?? DEPLOYMENT_METADATA_UNAVAILABLE,
    fullValue: value,
  };
}

// Build the four always-rendered rows. Order is fixed: web then API commit (the two
// most important for stale-deploy diagnosis), then the build/deploy times.
export function buildDeploymentVersionFields(
  input: DeploymentVersionInput,
): DeploymentVersionField[] {
  return [
    commitField('web-commit', 'Web commit', input.webCommit),
    commitField('api-commit', 'API commit', input.apiCommit),
    timestampField('web-build-time', 'Web build time', input.webBuildTime),
    timestampField('api-deploy-time', 'API deployment time', input.apiDeployTime),
  ];
}
