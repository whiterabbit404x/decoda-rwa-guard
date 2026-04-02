#!/usr/bin/env bash
set -euo pipefail

required=(
  STAGING_WEB_BASE_URL
  STAGING_API_BASE_URL
  STAGING_EMAIL_VERIFICATION_MODE
  EXPORTS_STORAGE_BACKEND
  BACKGROUND_JOBS_MODE
)
for var in "${required[@]}"; do
  if [[ -z "${!var:-}" ]]; then
    echo "Missing required env var: $var" >&2
    exit 2
  fi
done

run_id="$(date -u +%Y%m%dT%H%M%SZ)"
out="artifacts/staging-evidence/$run_id"
mkdir -p "$out"/{screenshots,json,logs,exports}

printf '# Staging evidence summary\n\nRun ID: %s\n' "$run_id" > "$out/summary.md"
printf 'Web: %s\nAPI: %s\n' "$STAGING_WEB_BASE_URL" "$STAGING_API_BASE_URL" >> "$out/summary.md"

echo "Evidence directory prepared at $out"
