"""Shared release attestation identity and timestamp helpers."""
from __future__ import annotations

import os
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DEFAULT_MAX_EVIDENCE_AGE_SECONDS = 24 * 60 * 60


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def parse_timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError('timestamp is missing')
    parsed = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('timestamp must include a timezone')
    return parsed.astimezone(timezone.utc)


def git_sha(repo_root: Path) -> str:
    env_sha = (os.getenv('RELEASE_COMMIT_SHA') or os.getenv('GITHUB_SHA') or '').strip().lower()
    if SHA_RE.fullmatch(env_sha):
        return env_sha
    try:
        value = subprocess.check_output(
            ['git', 'rev-parse', 'HEAD'], cwd=repo_root, text=True, stderr=subprocess.DEVNULL
        ).strip().lower()
    except (subprocess.CalledProcessError, FileNotFoundError):
        value = 'unknown'
    return value


def build_attestation_context(repo_root: Path, environment: str) -> dict[str, str]:
    """Return stable identity fields shared by every artifact in one release run."""
    sha = git_sha(repo_root)
    ci_run_id = (os.getenv('CI_RUN_ID') or os.getenv('GITHUB_RUN_ID') or f'local-{sha[:12]}').strip()
    deployment_id = (
        os.getenv('RELEASE_DEPLOYMENT_ID')
        or os.getenv('DEPLOYMENT_ID')
        or os.getenv('GITHUB_DEPLOYMENT_ID')
        or f'{environment}-{ci_run_id}-{sha[:12]}'
    ).strip()
    resolved_environment = environment.strip().lower()
    started_at = (
        os.getenv('RELEASE_EVIDENCE_STARTED_AT')
        or os.getenv('GITHUB_RUN_STARTED_AT')
        or format_timestamp(utc_now() - timedelta(seconds=1))
    ).strip()
    completed_at = format_timestamp(utc_now())
    return {
        'commit_sha': sha,
        'deployment_id': deployment_id,
        'ci_run_id': ci_run_id,
        'environment': resolved_environment,
        'evidence_started_at': started_at,
        'evidence_completed_at': completed_at,
    }
