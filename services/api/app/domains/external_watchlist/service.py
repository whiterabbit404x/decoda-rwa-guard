"""Database access for the External Watchlist.

Every statement here reads or writes ONLY the ``external_watchlist*`` tables
from migration 0157. None of them touches a customer table (targets,
telemetry_events, threat_detections, alerts, incidents, response actions), so
no customer read path can surface external data and no external action can
reach customer data. The single exception is the explicit founder conversion
in ``conversion.py``, which writes into the ONE Pilot workspace it has just
provisioned — never into any other tenant.

Connections are passed in; this module never opens or commits one.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from services.api.app.domains.external_watchlist import config as ewc

TABLES = (
    'external_watchlists',
    'external_watchlist_targets',
    'external_watchlist_backfills',
    'external_watchlist_events',
    'external_watchlist_baselines',
    'external_watchlist_findings',
    'external_watchlist_evidence',
    'external_watchlist_conversions',
    'external_watchlist_worker_state',
)

_SLUG_RE = re.compile(r'[^a-z0-9]+')

#: Upper bound on protocols a single listing loads before status filtering.
#: V1 is a founder console; this keeps the read bounded without a cursor.
MAX_LISTING_ROWS = 1000


def _dumps(value: Any) -> str:
    return json.dumps(value, default=str, separators=(',', ':'))


def _row(row: Any) -> dict[str, Any] | None:
    if row is None:
        return None
    return dict(row) if not isinstance(row, dict) else row


def _rows(rows: Any) -> list[dict[str, Any]]:
    return [dict(row) if not isinstance(row, dict) else row for row in (rows or [])]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def slugify(name: str) -> str:
    slug = _SLUG_RE.sub('-', str(name or '').strip().lower()).strip('-')
    return (slug or 'protocol')[:140].strip('-') or 'protocol'


def schema_ready(connection: Any) -> bool:
    try:
        row = _row(connection.execute(
            """SELECT COUNT(*) AS table_count FROM information_schema.tables
               WHERE table_schema = 'public' AND table_name = ANY(%s)""",
            (list(TABLES),),
        ).fetchone())
    except Exception:
        return False
    return int((row or {}).get('table_count') or 0) >= len(TABLES)


# ── watchlists ───────────────────────────────────────────────────────────────
def find_active_watchlist_by_slug(connection: Any, slug: str) -> dict[str, Any] | None:
    return _row(connection.execute(
        'SELECT id, name FROM external_watchlists WHERE slug = %s AND deleted_at IS NULL',
        (slug,),
    ).fetchone())


def insert_watchlist(
    connection: Any, *, name: str, slug: str, website_url: str | None, description: str | None,
    backfill_days: int, detection_profiles: list[str], detection_config: dict[str, Any],
    created_by: str | None, status: str,
) -> dict[str, Any]:
    return _row(connection.execute(
        """
        INSERT INTO external_watchlists (
            id, name, slug, website_url, description, status, monitoring_enabled,
            backfill_days, detection_profiles, detection_config, disclaimer_version,
            monitoring_scope, execution_authority, created_by, created_at, updated_at
        ) VALUES (%s, %s, %s, %s, %s, %s, TRUE, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s, NOW(), NOW())
        RETURNING *
        """,
        (
            str(uuid.uuid4()), name, slug, website_url, description, status,
            int(backfill_days), _dumps(detection_profiles), _dumps(detection_config),
            ewc.DISCLAIMER_VERSION, ewc.MONITORING_SCOPE_EXTERNAL_PUBLIC,
            ewc.EXECUTION_AUTHORITY_NONE, created_by,
        ),
    ).fetchone()) or {}


def get_watchlist(connection: Any, watchlist_id: str, *, for_update: bool = False) -> dict[str, Any] | None:
    lock = ' FOR UPDATE' if for_update else ''
    return _row(connection.execute(
        f'SELECT * FROM external_watchlists WHERE id = %s AND deleted_at IS NULL{lock}',
        (watchlist_id,),
    ).fetchone())


def list_watchlist_rows(connection: Any, *, search: str | None, network: str | None) -> list[dict[str, Any]]:
    clauses = ['w.deleted_at IS NULL']
    params: list[Any] = []
    if search:
        clauses.append('(w.name ILIKE %s OR w.slug ILIKE %s OR COALESCE(w.website_url, \'\') ILIKE %s)')
        pattern = f'%{search}%'
        params.extend([pattern, pattern, pattern])
    if network:
        clauses.append(
            'EXISTS (SELECT 1 FROM external_watchlist_targets nt WHERE nt.watchlist_id = w.id '
            'AND nt.removed_at IS NULL AND nt.network = %s)'
        )
        params.append(network)
    params.append(MAX_LISTING_ROWS)
    return _rows(connection.execute(
        f"""
        SELECT w.* FROM external_watchlists w
        WHERE {' AND '.join(clauses)}
        ORDER BY w.created_at DESC, w.id DESC
        LIMIT %s
        """,
        tuple(params),
    ).fetchall())


def update_watchlist_fields(connection: Any, watchlist_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
    allowed = {
        'name': '%s', 'slug': '%s', 'website_url': '%s', 'description': '%s',
        'monitoring_enabled': '%s', 'detection_profiles': '%s::jsonb', 'detection_config': '%s::jsonb',
        'status': '%s',
    }
    sets, params = [], []
    for key, value in fields.items():
        if key not in allowed:
            raise ValueError(f'field {key} is not updatable')
        sets.append(f'{key} = {allowed[key]}')
        params.append(_dumps(value) if key in ('detection_profiles', 'detection_config') else value)
    if not sets:
        return get_watchlist(connection, watchlist_id)
    params.append(watchlist_id)
    return _row(connection.execute(
        f"""UPDATE external_watchlists SET {', '.join(sets)}, updated_at = NOW()
            WHERE id = %s AND deleted_at IS NULL RETURNING *""",
        tuple(params),
    ).fetchone())


def soft_delete_watchlist(connection: Any, watchlist_id: str) -> None:
    connection.execute(
        """UPDATE external_watchlists SET deleted_at = NOW(), monitoring_enabled = FALSE,
               status = 'paused', updated_at = NOW()
           WHERE id = %s AND deleted_at IS NULL""",
        (watchlist_id,),
    )
    connection.execute(
        """UPDATE external_watchlist_targets SET removed_at = NOW(), monitoring_enabled = FALSE, updated_at = NOW()
           WHERE watchlist_id = %s AND removed_at IS NULL""",
        (watchlist_id,),
    )


def mark_converted(connection: Any, watchlist_id: str, workspace_id: str) -> None:
    connection.execute(
        """UPDATE external_watchlists SET converted_workspace_id = %s, converted_at = NOW(), updated_at = NOW()
           WHERE id = %s""",
        (workspace_id, watchlist_id),
    )


# ── targets ──────────────────────────────────────────────────────────────────
def find_active_target(connection: Any, *, watchlist_id: str, chain_id: int, address: str) -> dict[str, Any] | None:
    return _row(connection.execute(
        """SELECT id FROM external_watchlist_targets
           WHERE watchlist_id = %s AND chain_id = %s AND address = %s AND removed_at IS NULL""",
        (watchlist_id, int(chain_id), address),
    ).fetchone())


def find_target_elsewhere(connection: Any, *, chain_id: int, address: str, exclude_watchlist_id: str | None) -> dict[str, Any] | None:
    """The same public address already monitored under another protocol."""
    return _row(connection.execute(
        """SELECT t.id, t.watchlist_id, w.name AS watchlist_name
           FROM external_watchlist_targets t
           JOIN external_watchlists w ON w.id = t.watchlist_id AND w.deleted_at IS NULL
           WHERE t.chain_id = %s AND t.address = %s AND t.removed_at IS NULL
             AND (%s::uuid IS NULL OR t.watchlist_id <> %s::uuid)
           LIMIT 1""",
        (int(chain_id), address, exclude_watchlist_id, exclude_watchlist_id),
    ).fetchone())


def insert_target(
    connection: Any, *, watchlist_id: str, target_type: str, network: str, chain_id: int, address: str,
    label: str | None, contract_type: str | None, has_code: bool | None, live_start_block: int | None,
    runtime_state: dict[str, Any], created_by: str | None,
) -> dict[str, Any]:
    return _row(connection.execute(
        """
        INSERT INTO external_watchlist_targets (
            id, watchlist_id, target_type, network, chain_id, address, label, abi_source,
            contract_type, monitoring_enabled, monitoring_scope, execution_authority, has_code,
            live_start_block, last_processed_block, runtime_state, created_by, created_at, updated_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'standard_events', %s, TRUE, %s, %s, %s, %s, NULL,
                  %s::jsonb, %s, NOW(), NOW())
        RETURNING *
        """,
        (
            # last_processed_block starts NULL: nothing has been read yet, and
            # "last block processed" must not claim a block before a poll does.
            # The live tail begins at live_start_block (the tip seen at creation + 1).
            str(uuid.uuid4()), watchlist_id, target_type, network, int(chain_id), address, label,
            contract_type, ewc.MONITORING_SCOPE_EXTERNAL_PUBLIC, ewc.EXECUTION_AUTHORITY_NONE, has_code,
            live_start_block, _dumps(runtime_state), created_by,
        ),
    ).fetchone()) or {}


def list_targets(connection: Any, watchlist_ids: list[str], *, include_removed: bool = False) -> list[dict[str, Any]]:
    if not watchlist_ids:
        return []
    removed = '' if include_removed else ' AND removed_at IS NULL'
    return _rows(connection.execute(
        f"""SELECT * FROM external_watchlist_targets
            WHERE watchlist_id = ANY(%s::uuid[]){removed}
            ORDER BY created_at ASC, id ASC""",
        (list(watchlist_ids),),
    ).fetchall())


def get_target(connection: Any, *, watchlist_id: str, target_id: str) -> dict[str, Any] | None:
    return _row(connection.execute(
        """SELECT * FROM external_watchlist_targets
           WHERE id = %s AND watchlist_id = %s AND removed_at IS NULL""",
        (target_id, watchlist_id),
    ).fetchone())


def update_target_fields(connection: Any, *, watchlist_id: str, target_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
    allowed = {'label', 'monitoring_enabled', 'contract_type', 'has_code', 'runtime_state'}
    sets, params = [], []
    for key, value in fields.items():
        if key not in allowed:
            raise ValueError(f'field {key} is not updatable')
        if key == 'runtime_state':
            sets.append('runtime_state = %s::jsonb')
            params.append(_dumps(value))
        else:
            sets.append(f'{key} = %s')
            params.append(value)
    if not sets:
        return get_target(connection, watchlist_id=watchlist_id, target_id=target_id)
    params.extend([target_id, watchlist_id])
    return _row(connection.execute(
        f"""UPDATE external_watchlist_targets SET {', '.join(sets)}, updated_at = NOW()
            WHERE id = %s AND watchlist_id = %s AND removed_at IS NULL RETURNING *""",
        tuple(params),
    ).fetchone())


def remove_target(connection: Any, *, watchlist_id: str, target_id: str) -> dict[str, Any] | None:
    row = _row(connection.execute(
        """UPDATE external_watchlist_targets
           SET removed_at = NOW(), monitoring_enabled = FALSE, lease_owner = NULL,
               lease_expires_at = NULL, updated_at = NOW()
           WHERE id = %s AND watchlist_id = %s AND removed_at IS NULL RETURNING *""",
        (target_id, watchlist_id),
    ).fetchone())
    if row is not None:
        # An open history scan for a removed target stops truthfully as partial.
        connection.execute(
            """UPDATE external_watchlist_backfills
               SET status = CASE WHEN scanned_blocks > 0 THEN 'partial' ELSE 'failed' END,
                   status_reason = 'target_removed', completed_at = NOW(), updated_at = NOW(),
                   lease_owner = NULL, lease_expires_at = NULL
               WHERE target_id = %s AND status IN ('pending', 'running')""",
            (target_id,),
        )
    return row


# ── backfills ────────────────────────────────────────────────────────────────
def open_backfill_for_target(connection: Any, target_id: str) -> dict[str, Any] | None:
    return _row(connection.execute(
        """SELECT id, status FROM external_watchlist_backfills
           WHERE target_id = %s AND status IN ('pending', 'running') LIMIT 1""",
        (target_id,),
    ).fetchone())


def create_backfill(
    connection: Any, *, watchlist_id: str, target: dict[str, Any], days: int, requested_by: str | None,
) -> dict[str, Any]:
    return _row(connection.execute(
        """
        INSERT INTO external_watchlist_backfills (
            id, watchlist_id, target_id, status, requested_days, requested_by, network, chain_id,
            created_at, updated_at
        ) VALUES (%s, %s, %s, 'pending', %s, %s, %s, %s, NOW(), NOW())
        RETURNING *
        """,
        (
            str(uuid.uuid4()), watchlist_id, str(target['id']), int(days), requested_by,
            target['network'], int(target['chain_id']),
        ),
    ).fetchone()) or {}


def latest_backfills(connection: Any, watchlist_ids: list[str]) -> list[dict[str, Any]]:
    if not watchlist_ids:
        return []
    return _rows(connection.execute(
        """SELECT DISTINCT ON (target_id) *
           FROM external_watchlist_backfills
           WHERE watchlist_id = ANY(%s::uuid[])
           ORDER BY target_id, created_at DESC, id DESC""",
        (list(watchlist_ids),),
    ).fetchall())


def backfills_for_target(connection: Any, target_id: str) -> list[dict[str, Any]]:
    return _rows(connection.execute(
        'SELECT * FROM external_watchlist_backfills WHERE target_id = %s ORDER BY created_at ASC, id ASC',
        (target_id,),
    ).fetchall())


def list_backfills(connection: Any, watchlist_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
    return _rows(connection.execute(
        """SELECT * FROM external_watchlist_backfills WHERE watchlist_id = %s
           ORDER BY created_at DESC, id DESC LIMIT %s""",
        (watchlist_id, int(limit)),
    ).fetchall())


# ── events ───────────────────────────────────────────────────────────────────
def insert_event(connection: Any, event: dict[str, Any]) -> str | None:
    """Insert one observed log. Returns the new id, or None when this exact log
    (target, tx, log index) was already stored — the dedupe guarantee."""
    row = _row(connection.execute(
        """
        INSERT INTO external_watchlist_events (
            id, watchlist_id, target_id, monitoring_scope, network, chain_id, contract_address,
            block_number, block_hash, tx_hash, log_index, event_name, event_type, event_category,
            topic0, decoded, decode_status, initiator, raw_log, payload_sha256, ingest_source,
            backfill_id, block_timestamp, observed_at, observed_at_source, ingested_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s,
                  %s::jsonb, %s, %s, %s, %s, %s, %s, NOW())
        ON CONFLICT (target_id, tx_hash, log_index) DO NOTHING
        RETURNING id
        """,
        (
            str(uuid.uuid4()), event['watchlist_id'], event['target_id'], ewc.MONITORING_SCOPE_EXTERNAL_PUBLIC,
            event['network'], int(event['chain_id']), event['contract_address'], int(event['block_number']),
            event.get('block_hash'), event['tx_hash'], int(event['log_index']), event['event_name'],
            event['event_type'], event['event_category'], event['topic0'], _dumps(event.get('decoded') or {}),
            event['decode_status'], event.get('initiator'), _dumps(event.get('raw_log') or {}),
            event['payload_sha256'], event['ingest_source'], event.get('backfill_id'),
            event.get('block_timestamp'), event['observed_at'], event['observed_at_source'],
        ),
    ).fetchone())
    return str(row['id']) if row else None


def list_events(
    connection: Any, *, watchlist_id: str, limit: int, offset: int, category: str | None = None,
    target_id: str | None = None,
) -> tuple[list[dict[str, Any]], int]:
    clauses = ['e.watchlist_id = %s']
    params: list[Any] = [watchlist_id]
    if category:
        clauses.append('e.event_category = %s')
        params.append(category)
    if target_id:
        clauses.append('e.target_id = %s')
        params.append(target_id)
    where = ' AND '.join(clauses)
    total_row = _row(connection.execute(
        f'SELECT COUNT(*) AS total FROM external_watchlist_events e WHERE {where}', tuple(params),
    ).fetchone())
    rows = _rows(connection.execute(
        f"""
        SELECT e.*, t.label AS target_label, t.address AS target_address, t.target_type
        FROM external_watchlist_events e
        JOIN external_watchlist_targets t ON t.id = e.target_id
        WHERE {where}
        ORDER BY e.observed_at DESC, e.block_number DESC, e.log_index DESC
        LIMIT %s OFFSET %s
        """,
        tuple(params + [int(limit), int(offset)]),
    ).fetchall())
    return rows, int((total_row or {}).get('total') or 0)


def events_by_ids(connection: Any, *, watchlist_id: str, event_ids: list[str]) -> list[dict[str, Any]]:
    if not event_ids:
        return []
    return _rows(connection.execute(
        """SELECT * FROM external_watchlist_events
           WHERE watchlist_id = %s AND id = ANY(%s::uuid[])
           ORDER BY block_number ASC, log_index ASC""",
        (watchlist_id, list(event_ids)),
    ).fetchall())


def events_for_tx(connection: Any, *, watchlist_id: str, tx_hash: str, limit: int = 25) -> list[dict[str, Any]]:
    return _rows(connection.execute(
        """SELECT * FROM external_watchlist_events WHERE watchlist_id = %s AND tx_hash = %s
           ORDER BY log_index ASC LIMIT %s""",
        (watchlist_id, tx_hash, int(limit)),
    ).fetchall())


def watchlist_activity(connection: Any, watchlist_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Latest observed event and latest finding per protocol, for the listing."""
    if not watchlist_ids:
        return {}
    latest_events = _rows(connection.execute(
        """SELECT DISTINCT ON (watchlist_id) watchlist_id, event_name, event_category, observed_at
           FROM external_watchlist_events WHERE watchlist_id = ANY(%s::uuid[])
           ORDER BY watchlist_id, observed_at DESC, block_number DESC, log_index DESC""",
        (list(watchlist_ids),),
    ).fetchall())
    latest_findings = _rows(connection.execute(
        """SELECT DISTINCT ON (watchlist_id) watchlist_id, title, severity, finding_class, observed_at, detected_at
           FROM external_watchlist_findings WHERE watchlist_id = ANY(%s::uuid[])
           ORDER BY watchlist_id, detected_at DESC, id DESC""",
        (list(watchlist_ids),),
    ).fetchall())
    result: dict[str, dict[str, Any]] = {str(w): {'latest_event': None, 'latest_finding': None} for w in watchlist_ids}
    for row in latest_events:
        result.setdefault(str(row['watchlist_id']), {})['latest_event'] = row
    for row in latest_findings:
        result.setdefault(str(row['watchlist_id']), {})['latest_finding'] = row
    return result


def overview_counts(connection: Any, watchlist_id: str, *, now: datetime | None = None) -> dict[str, int]:
    moment = now or utc_now()
    events = _row(connection.execute(
        """SELECT COUNT(*) AS total,
                  COUNT(*) FILTER (WHERE observed_at >= %s) AS last_24h
           FROM external_watchlist_events WHERE watchlist_id = %s""",
        (moment - timedelta(hours=24), watchlist_id),
    ).fetchone()) or {}
    findings = _row(connection.execute(
        """SELECT COUNT(*) AS total,
                  COUNT(*) FILTER (WHERE COALESCE(observed_at, detected_at) >= %s) AS last_30d,
                  COUNT(*) FILTER (WHERE status = 'new') AS open_new
           FROM external_watchlist_findings WHERE watchlist_id = %s""",
        (moment - timedelta(days=30), watchlist_id),
    ).fetchone()) or {}
    return {
        'events_total': int(events.get('total') or 0),
        'events_24h': int(events.get('last_24h') or 0),
        'findings_total': int(findings.get('total') or 0),
        'findings_30d': int(findings.get('last_30d') or 0),
        'findings_new': int(findings.get('open_new') or 0),
    }


def recent_events(connection: Any, watchlist_id: str, *, limit: int = 8) -> list[dict[str, Any]]:
    return _rows(connection.execute(
        """SELECT id, event_name, event_category, contract_address, tx_hash, block_number, observed_at, network
           FROM external_watchlist_events WHERE watchlist_id = %s
           ORDER BY observed_at DESC, block_number DESC, log_index DESC LIMIT %s""",
        (watchlist_id, int(limit)),
    ).fetchall())


# ── baselines and rule state ─────────────────────────────────────────────────
def load_baselines(connection: Any, target_id: str) -> list[dict[str, Any]]:
    return _rows(connection.execute(
        """SELECT token_address, stat_kind, sample_count, mean, max_value, updated_block
           FROM external_watchlist_baselines WHERE target_id = %s""",
        (target_id,),
    ).fetchall())


def save_baseline(connection: Any, *, target_id: str, token: str, kind: str, sample_count: int,
                  mean: Any, max_value: Any, updated_block: int | None) -> None:
    connection.execute(
        """
        INSERT INTO external_watchlist_baselines (target_id, token_address, stat_kind, sample_count, mean, max_value, updated_block, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
        ON CONFLICT (target_id, token_address, stat_kind) DO UPDATE SET
            sample_count = EXCLUDED.sample_count, mean = EXCLUDED.mean, max_value = EXCLUDED.max_value,
            updated_block = EXCLUDED.updated_block, updated_at = NOW()
        """,
        (target_id, token, kind, int(sample_count), str(mean), None if max_value is None else str(max_value), updated_block),
    )


def save_backfill_rule_state(connection: Any, backfill_id: str, rule_state: dict[str, Any]) -> None:
    connection.execute(
        'UPDATE external_watchlist_backfills SET rule_state = %s::jsonb, updated_at = NOW() WHERE id = %s',
        (_dumps(rule_state), backfill_id),
    )


def save_runtime_state(connection: Any, target_id: str, runtime_state: dict[str, Any]) -> None:
    connection.execute(
        'UPDATE external_watchlist_targets SET runtime_state = %s::jsonb, updated_at = NOW() WHERE id = %s',
        (_dumps(runtime_state), target_id),
    )


# ── findings ─────────────────────────────────────────────────────────────────
def insert_finding(connection: Any, finding: dict[str, Any]) -> str | None:
    row = _row(connection.execute(
        """
        INSERT INTO external_watchlist_findings (
            id, watchlist_id, target_id, monitoring_scope, execution_authority, detector_version,
            rule_key, detection_profile, finding_type, finding_class, title, severity, confidence,
            status, network, chain_id, contract_address, tx_hash, block_number, log_index, observed_at,
            initiator, primary_event_id, related_event_ids, decoded, previous_state, new_state,
            explanation, ai_analysis, score_inputs, dedupe_key, detected_at, created_at, updated_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'new', %s, %s, %s, %s, %s, %s, %s,
                  %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s::jsonb, %s::jsonb, %s,
                  NOW(), NOW(), NOW())
        ON CONFLICT (watchlist_id, dedupe_key) DO NOTHING
        RETURNING id
        """,
        (
            str(uuid.uuid4()), finding['watchlist_id'], finding.get('target_id'),
            ewc.MONITORING_SCOPE_EXTERNAL_PUBLIC, ewc.EXECUTION_AUTHORITY_NONE, ewc.DETECTOR_VERSION,
            finding['rule_key'], finding['detection_profile'], finding['finding_type'], finding['finding_class'],
            finding['title'], finding['severity'], float(finding.get('confidence') or 0),
            finding['network'], int(finding['chain_id']), finding.get('contract_address'), finding.get('tx_hash'),
            finding.get('block_number'), finding.get('log_index'), finding.get('observed_at'),
            finding.get('initiator'), finding.get('primary_event_id'), _dumps(finding.get('related_event_ids') or []),
            _dumps(finding.get('decoded') or {}),
            None if finding.get('previous_state') is None else _dumps(finding['previous_state']),
            None if finding.get('new_state') is None else _dumps(finding['new_state']),
            finding['explanation'], _dumps(finding.get('ai_analysis') or {}), _dumps(finding.get('score_inputs') or {}),
            finding['dedupe_key'],
        ),
    ).fetchone())
    return str(row['id']) if row else None


def list_findings(
    connection: Any, *, watchlist_id: str, limit: int, offset: int, status: str | None = None,
    severity: str | None = None,
) -> tuple[list[dict[str, Any]], int]:
    clauses = ['f.watchlist_id = %s']
    params: list[Any] = [watchlist_id]
    if status:
        clauses.append('f.status = %s')
        params.append(status)
    if severity:
        clauses.append('f.severity = %s')
        params.append(severity)
    where = ' AND '.join(clauses)
    total_row = _row(connection.execute(
        f'SELECT COUNT(*) AS total FROM external_watchlist_findings f WHERE {where}', tuple(params),
    ).fetchone())
    rows = _rows(connection.execute(
        f"""
        SELECT f.*, t.label AS target_label, t.address AS target_address, t.target_type
        FROM external_watchlist_findings f
        LEFT JOIN external_watchlist_targets t ON t.id = f.target_id
        WHERE {where}
        ORDER BY f.detected_at DESC, f.id DESC
        LIMIT %s OFFSET %s
        """,
        tuple(params + [int(limit), int(offset)]),
    ).fetchall())
    return rows, int((total_row or {}).get('total') or 0)


def get_finding(connection: Any, *, watchlist_id: str, finding_id: str) -> dict[str, Any] | None:
    return _row(connection.execute(
        """SELECT f.*, t.label AS target_label, t.address AS target_address, t.target_type
           FROM external_watchlist_findings f
           LEFT JOIN external_watchlist_targets t ON t.id = f.target_id
           WHERE f.id = %s AND f.watchlist_id = %s""",
        (finding_id, watchlist_id),
    ).fetchone())


def update_finding_status(
    connection: Any, *, watchlist_id: str, finding_id: str, status: str, note: str | None, user_id: str | None,
) -> dict[str, Any] | None:
    return _row(connection.execute(
        """UPDATE external_watchlist_findings
           SET status = %s, status_note = COALESCE(%s, status_note), status_updated_by = %s,
               status_updated_at = NOW(), updated_at = NOW()
           WHERE id = %s AND watchlist_id = %s RETURNING *""",
        (status, note, user_id, finding_id, watchlist_id),
    ).fetchone())


# ── evidence ─────────────────────────────────────────────────────────────────
def insert_evidence(connection: Any, *, watchlist_id: str, finding_id: str | None, package: dict[str, Any],
                    generated_by: str | None, source_evidence_id: str | None = None) -> dict[str, Any]:
    return _row(connection.execute(
        """
        INSERT INTO external_watchlist_evidence (
            id, watchlist_id, finding_id, package_type, monitoring_scope, source_evidence_id, manifest, seal,
            files, manifest_sha256, evidence_sha256, signature_algorithm, disclaimer_version, generated_by,
            generated_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        (
            package['id'], watchlist_id, finding_id, package['package_type'], ewc.MONITORING_SCOPE_EXTERNAL_PUBLIC,
            source_evidence_id, _dumps(package['manifest']), _dumps(package['seal']), _dumps(package['files']),
            package['manifest_sha256'], package['evidence_sha256'], package.get('signature_algorithm'),
            ewc.DISCLAIMER_VERSION, generated_by, package['generated_at'],
        ),
    ).fetchone()) or {}


def list_evidence(connection: Any, *, watchlist_id: str, limit: int, offset: int) -> tuple[list[dict[str, Any]], int]:
    total_row = _row(connection.execute(
        'SELECT COUNT(*) AS total FROM external_watchlist_evidence WHERE watchlist_id = %s', (watchlist_id,),
    ).fetchone())
    rows = _rows(connection.execute(
        """SELECT ev.id, ev.watchlist_id, ev.finding_id, ev.package_type, ev.source_evidence_id,
                  ev.manifest_sha256, ev.evidence_sha256, ev.signature_algorithm, ev.disclaimer_version,
                  ev.generated_at, ev.monitoring_scope, f.title AS finding_title, f.tx_hash AS finding_tx_hash
           FROM external_watchlist_evidence ev
           LEFT JOIN external_watchlist_findings f ON f.id = ev.finding_id
           WHERE ev.watchlist_id = %s
           ORDER BY ev.generated_at DESC, ev.id DESC LIMIT %s OFFSET %s""",
        (watchlist_id, int(limit), int(offset)),
    ).fetchall())
    return rows, int((total_row or {}).get('total') or 0)


def get_evidence(connection: Any, *, watchlist_id: str, evidence_id: str) -> dict[str, Any] | None:
    return _row(connection.execute(
        'SELECT * FROM external_watchlist_evidence WHERE id = %s AND watchlist_id = %s',
        (evidence_id, watchlist_id),
    ).fetchone())


def latest_evidence_package(connection: Any, *, watchlist_id: str, finding_id: str) -> dict[str, Any] | None:
    return _row(connection.execute(
        """SELECT * FROM external_watchlist_evidence
           WHERE watchlist_id = %s AND finding_id = %s AND package_type = 'evidence_package'
           ORDER BY generated_at DESC, id DESC LIMIT 1""",
        (watchlist_id, finding_id),
    ).fetchone())


# ── conversion ───────────────────────────────────────────────────────────────
def get_conversion(connection: Any, watchlist_id: str) -> dict[str, Any] | None:
    return _row(connection.execute(
        'SELECT * FROM external_watchlist_conversions WHERE watchlist_id = %s', (watchlist_id,),
    ).fetchone())


def insert_conversion(connection: Any, *, watchlist_id: str, organization_id: str, workspace_id: str,
                      copied_targets: list[dict[str, Any]], evaluation_days: int, evaluation_expires_at: Any,
                      converted_by: str | None) -> dict[str, Any]:
    return _row(connection.execute(
        """
        INSERT INTO external_watchlist_conversions (
            id, watchlist_id, organization_id, workspace_id, copied_targets, evaluation_days,
            evaluation_expires_at, converted_by, converted_at
        ) VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, NOW())
        RETURNING *
        """,
        (str(uuid.uuid4()), watchlist_id, organization_id, workspace_id, _dumps(copied_targets),
         int(evaluation_days), evaluation_expires_at, converted_by),
    ).fetchone()) or {}


# ── worker state ─────────────────────────────────────────────────────────────
def latest_worker_heartbeat(connection: Any) -> datetime | None:
    try:
        row = _row(connection.execute(
            'SELECT MAX(heartbeat_at) AS heartbeat_at FROM external_watchlist_worker_state',
        ).fetchone())
    except Exception:
        return None
    return (row or {}).get('heartbeat_at')


def record_worker_heartbeat(connection: Any, *, worker_name: str, summary: dict[str, Any] | None = None,
                            failed: bool = False, completed: bool = False) -> None:
    connection.execute(
        """
        INSERT INTO external_watchlist_worker_state (worker_name, heartbeat_at, last_cycle_at, last_completed_at,
                                                     consecutive_failures, last_cycle_summary, updated_at)
        VALUES (%s, NOW(), NOW(), CASE WHEN %s THEN NOW() ELSE NULL END, CASE WHEN %s THEN 1 ELSE 0 END, %s::jsonb, NOW())
        ON CONFLICT (worker_name) DO UPDATE SET
            heartbeat_at = NOW(),
            last_cycle_at = NOW(),
            last_completed_at = CASE WHEN %s THEN NOW() ELSE external_watchlist_worker_state.last_completed_at END,
            consecutive_failures = CASE WHEN %s THEN external_watchlist_worker_state.consecutive_failures + 1 ELSE 0 END,
            last_cycle_summary = EXCLUDED.last_cycle_summary,
            updated_at = NOW()
        """,
        (worker_name, completed, failed, _dumps(summary or {}), completed, failed),
    )
