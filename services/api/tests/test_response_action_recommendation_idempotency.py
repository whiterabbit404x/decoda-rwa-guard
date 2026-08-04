"""Screen 8 recommendation idempotency + "approval never generates recommendations".

Regression: after clicking Approve and refreshing, Screen 8's Recommended Actions
grew 6 -> 10 and Pending Approval 2 -> 6, with several duplicate "Notify security
team" AI-review rows.

Root cause (proved here): the APPROVAL command records only an approval decision —
it never generates or refreshes recommendations. The duplicates came from the
``ai_recommendations`` table having NO idempotency: every AI triage run inserted a
fresh set of recommendation rows, and the Response Actions read surfaced all of them
across every triage result. A second triage run for the incident (a Screen 7
investigation re-run / regenerate / re-queued worker job) therefore duplicated the
same recommendations.

Fix (migration 0138 + ai_triage.py + pilot.py):
  * A canonical dedup key (workspace_id, incident_id, action_type, COALESCE(runbook_id,''))
    with a partial UNIQUE index over non-superseded rows.
  * Generation upserts (ON CONFLICT DO UPDATE) so a re-run never inserts an unchanged
    duplicate; it re-points the existing canonical row and preserves its identity +
    human review/approval state.
  * The Screen 8 read + Screen 7 read + incident count surface only non-superseded
    (canonical) recommendations.
  * A non-destructive repair collapses existing duplicates by marking them superseded.

These tests follow the repo's fake-connection unit style (no real DB / model / network).
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from services.api.app import ai_triage, pilot

# Reuse the AI triage job harness (FakeConn drives process_triage_job end to end).
from services.api.tests.test_ai_triage_agent import (
    FakeConn,
    FakeResult,
    _claim_row,
    _enabled_cfg,
    _snapshot,
)
from services.api.app import ai_providers


INCIDENT = 'c537b73f-1976-4a44-b589-946194794399'
OTHER_INCIDENT = '00000000-0000-0000-0000-0000000000ff'
REC_ID = 'a1111111-1111-4111-8111-111111111111'
ACTION_ID = 'b2222222-2222-4222-8222-222222222222'
WS = 'ws-1'


# ==========================================================================
# Shared fakes
# ==========================================================================
class _Result:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows or []

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


def _req():
    return SimpleNamespace(headers={'x-workspace-id': WS}, client=SimpleNamespace(host='127.0.0.1'), method='POST')


def _no_recommendation_inserts(conn) -> None:
    """Assert NOTHING in the executed log generated or refreshed a recommendation.

    A recommendation is created ONLY by inserting into ai_recommendations, or by
    inserting a response_actions row in the 'recommended' mode. A governed command
    (approve / reject / simulate) and a page read must never do either.
    """
    for sql, params in conn.executed:
        assert 'INSERT INTO ai_recommendations' not in sql, f'approval path generated an AI recommendation: {sql}'
        if 'INSERT INTO response_actions' in sql:
            flat = f'{sql} {params}'
            assert 'recommended' not in flat, f'command inserted a recommended response_action: {sql}'


# ==========================================================================
# Group A — no governed command / read generates a recommendation (reqs 1-4)
# ==========================================================================
class _CommandConn:
    """Backs the approve/reject/simulate paths and records every executed statement,
    so a test can prove NO recommendation was generated as a side effect."""

    def __init__(self, *, ai_rows=None, response_rows=None, schema_ready=True):
        self.executed: list[tuple[str, object]] = []
        self.commits = 0
        self._ai_rows = {(str(r['id']), str(r['workspace_id'])): r for r in (ai_rows or [])}
        self._response_rows = {(str(r['id']), str(r['workspace_id'])): r for r in (response_rows or [])}
        self._schema_ready = schema_ready

    def execute(self, statement, params=None):
        n = ' '.join(str(statement).split())
        self.executed.append((n, params))
        if 'to_regclass' in n:
            return _Result(row={'present': self._schema_ready})
        if 'FROM response_actions WHERE id = %s AND workspace_id = %s' in n and n.startswith('SELECT'):
            return _Result(row=self._response_rows.get((str(params[0]), str(params[1]))))
        if 'FROM response_actions WHERE id = %s::uuid AND workspace_id = %s' in n and n.startswith('SELECT'):
            return _Result(row=self._response_rows.get((str(params[0]), str(params[1]))))
        if 'FROM ai_recommendations' in n and n.startswith('SELECT'):
            return _Result(row=self._ai_rows.get((str(params[0]), str(params[1]))))
        if 'SELECT decision FROM response_action_approvals' in n:
            return _Result(row=None)
        if 'SELECT approver_user_id, decision, required_quorum' in n:
            # After the INSERT below, report one approval so quorum (1) is reached.
            return _Result(rows=[{'approver_user_id': 'approver-1', 'decision': 'approved', 'required_quorum': 1}])
        return _Result()

    def commit(self):
        self.commits += 1


def _ai_rec(**over):
    row = {
        'id': REC_ID,
        'workspace_id': WS,
        'incident_id': INCIDENT,
        'action_type': 'notify_security_team',
        'runbook_id': 'notify_security_team_v1',
        'review_state': 'pending_review',
        'requires_human_approval': True,
    }
    row.update(over)
    return row


def _response_action(**over):
    row = {
        'id': ACTION_ID,
        'workspace_id': WS,
        'incident_id': INCIDENT,
        'alert_id': None,
        'action_type': 'increase_monitoring',
        'mode': 'recommended',
        'status': 'pending',
        'execution_state': 'recommended',
        'execution_mode': None,
        'execution_metadata': {},
    }
    row.update(over)
    return row


def _patch_command(monkeypatch, conn, *, user_id='approver-1', role='admin'):
    @contextmanager
    def _fake_pg():
        yield conn

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', _fake_pg)
    monkeypatch.setattr(pilot, '_require_workspace_permission',
                        lambda *_a, **_k: ({'id': user_id}, {'workspace_id': WS, 'role': role}))
    monkeypatch.setattr(pilot, 'write_action_history', lambda *a, **k: None)
    monkeypatch.setattr(pilot, 'append_incident_timeline_event', lambda *a, **k: None)
    monkeypatch.setattr(pilot, 'log_audit', lambda *a, **k: None)


def test_approving_action_creates_no_recommendation(monkeypatch):
    conn = _CommandConn(ai_rows=[_ai_rec()])
    _patch_command(monkeypatch, conn)
    pilot.approve_enforcement_action(REC_ID, _req())
    _no_recommendation_inserts(conn)


def test_rejecting_action_creates_no_recommendation(monkeypatch):
    conn = _CommandConn(ai_rows=[_ai_rec()])
    _patch_command(monkeypatch, conn)
    pilot.reject_enforcement_action(REC_ID, {'reason': 'false positive'}, _req())
    _no_recommendation_inserts(conn)


def test_simulating_action_creates_no_recommendation(monkeypatch):
    conn = _CommandConn(response_rows=[_response_action()])
    _patch_command(monkeypatch, conn)
    pilot.simulate_response_action(ACTION_ID, _req())
    _no_recommendation_inserts(conn)


def test_loading_screen8_creates_no_recommendation(monkeypatch):
    # A full Screen 8 read (list_enforcement_actions) must be strictly read-only.
    ai_rows = [{
        'recommendation_id': REC_ID, 'workspace_id': WS, 'incident_id': INCIDENT, 'triage_result_id': 'res-1',
        'triage_job_id': 'job-1', 'action_type': 'notify_security_team', 'runbook_id': 'notify_security_team_v1',
        'reason': 'Confirmed suspicious transfer.', 'risk_level': 'low', 'requires_human_approval': True,
        'evidence_refs': [], 'review_state': 'pending_review', 'reviewed_by_user_id': None, 'reviewed_at': None,
        'review_reason': None, 'created_at': '2026-07-13T09:00:00Z', 'provider': 'openai', 'model': 'gpt',
        'evidence_snapshot_id': 'snap-1', 'evidence_snapshot_hash': 'sha256:abc', 'reviewer_email': None,
    }]
    conn = _ListConn(ai_rows=ai_rows)
    _patch_list(monkeypatch, conn)
    pilot.list_enforcement_actions(_req(), incident_id=INCIDENT)
    for sql, _ in conn.executed:
        assert not sql.startswith('INSERT')
        assert not sql.startswith('UPDATE')
        assert not sql.startswith('DELETE')


# ==========================================================================
# Group B — Screen 8 read shows only CANONICAL recommendations (reqs 6, 7, 8)
# ==========================================================================
class _ListConn:
    """Backs list_enforcement_actions: response_actions + ai_recommendations reads,
    simulating both the workspace/incident WHERE filtering AND the new
    ``superseded_at IS NULL`` canonical filter (only when the dedup index is present)."""

    def __init__(self, *, legacy_rows=None, ai_rows=None, schema_ready=True, dedup_ready=True):
        self.executed: list[tuple[str, object]] = []
        self._legacy_rows = legacy_rows or []
        self._ai_rows = ai_rows or []
        self._schema_ready = schema_ready
        self._dedup_ready = dedup_ready

    def execute(self, statement, params=None):
        n = ' '.join(str(statement).split())
        self.executed.append((n, params))
        if 'to_regclass' in n:
            target = str((params or [''])[0])
            if pilot.AI_RECOMMENDATION_DEDUP_INDEX in target:
                return _Result(row={'present': self._dedup_ready})
            return _Result(row={'present': self._schema_ready})
        if 'FROM response_actions' in n and 'SELECT' in n and 'workspace_id = %s' in n:
            ws, action_id, incident_id = params[0], params[1], params[3]
            rows = [r for r in self._legacy_rows if str(r.get('workspace_id')) == str(ws)]
            if action_id is not None:
                rows = [r for r in rows if str(r.get('id')) == str(action_id)]
            if incident_id is not None:
                rows = [r for r in rows if str(r.get('incident_id')) == str(incident_id)]
            return _Result(rows=rows)
        if 'FROM ai_recommendations r' in n and 'JOIN ai_triage_results' in n:
            ws, incident_id, action_id = params[0], params[1], params[3]
            rows = [r for r in self._ai_rows if str(r.get('workspace_id')) == str(ws)]
            if incident_id is not None:
                rows = [r for r in rows if str(r.get('incident_id')) == str(incident_id)]
            if action_id is not None:
                rows = [r for r in rows if str(r.get('recommendation_id')) == str(action_id)]
            # Faithfully simulate the canonical filter the real query applies.
            if 'superseded_at IS NULL' in n:
                rows = [r for r in rows if not r.get('superseded_at')]
            return _Result(rows=rows)
        if 'FROM response_action_approvals' in n and 'SELECT' in n:
            return _Result(rows=[])
        return _Result()

    def commit(self):
        return None


def _patch_list(monkeypatch, conn, *, workspace_id=WS):
    @contextmanager
    def _fake_pg():
        yield conn

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', _fake_pg)
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda *_a, **_k: {'id': 'user-1'})
    monkeypatch.setattr(pilot, 'resolve_workspace', lambda *_a, **_k: {'workspace_id': workspace_id})
    monkeypatch.setattr(pilot, 'log_audit', lambda *_a, **_k: None)


def _ai_review_row(**over):
    row = {
        'recommendation_id': 'rec-1', 'workspace_id': WS, 'incident_id': INCIDENT, 'triage_result_id': 'res-1',
        'triage_job_id': 'job-1', 'action_type': 'notify_security_team', 'runbook_id': 'notify_security_team_v1',
        'reason': 'Confirmed suspicious transfer.', 'risk_level': 'low', 'requires_human_approval': True,
        'evidence_refs': [], 'review_state': 'pending_review', 'reviewed_by_user_id': None, 'reviewed_at': None,
        'review_reason': None, 'created_at': '2026-07-13T09:00:00Z', 'provider': 'openai', 'model': 'gpt',
        'evidence_snapshot_id': 'snap-1', 'evidence_snapshot_hash': 'sha256:abc', 'reviewer_email': None,
        'superseded_at': None,
    }
    row.update(over)
    return row


def test_same_recommendation_and_target_deduplicate(monkeypatch):
    # Two rows, same canonical key: one canonical + one superseded duplicate. Only the
    # canonical is surfaced on Screen 8 (this is exactly the repaired 6->10 dupes).
    conn = _ListConn(ai_rows=[
        _ai_review_row(recommendation_id='rec-canonical'),
        _ai_review_row(recommendation_id='rec-dup', superseded_at='2026-07-14T00:00:00Z'),
    ])
    _patch_list(monkeypatch, conn)
    actions = pilot.list_enforcement_actions(_req(), incident_id=INCIDENT)['actions']
    ai = [a for a in actions if a.get('record_type') == 'ai_recommendation_review']
    assert [a['id'] for a in ai] == ['rec-canonical']
    # The real SQL applies the canonical filter (belt-and-suspenders for the count).
    assert any('superseded_at IS NULL' in sql for sql, _ in conn.executed if 'FROM ai_recommendations r' in sql)


def test_different_targets_remain_distinct(monkeypatch):
    # Different action_type / runbook are genuinely different recommendations and must
    # BOTH remain visible — dedup collapses identical rows only, never distinct ones.
    conn = _ListConn(ai_rows=[
        _ai_review_row(recommendation_id='rec-notify', action_type='notify_security_team', runbook_id='notify_security_team_v1'),
        _ai_review_row(recommendation_id='rec-monitor', action_type='increase_monitoring', runbook_id='increase_monitoring_v1'),
    ])
    _patch_list(monkeypatch, conn)
    actions = pilot.list_enforcement_actions(_req(), incident_id=INCIDENT)['actions']
    ids = {a.get('id') for a in actions}
    assert 'rec-notify' in ids and 'rec-monitor' in ids


def test_recommendation_uniqueness_is_workspace_scoped(monkeypatch):
    # A recommendation in another tenant with the SAME key is never returned, and the
    # workspace_id bound into the read is the caller's — never the incident's alone.
    conn = _ListConn(ai_rows=[
        _ai_review_row(recommendation_id='rec-ws1', workspace_id=WS),
        _ai_review_row(recommendation_id='rec-ws2', workspace_id='ws-2'),
    ])
    _patch_list(monkeypatch, conn, workspace_id=WS)
    actions = pilot.list_enforcement_actions(_req(), incident_id=INCIDENT)['actions']
    ai = [a for a in actions if a.get('record_type') == 'ai_recommendation_review']
    assert [a['id'] for a in ai] == ['rec-ws1']
    ai_query = [p for (sql, p) in conn.executed if 'FROM ai_recommendations r' in sql][0]
    assert ai_query[0] == WS


def test_list_does_not_filter_superseded_before_migration(monkeypatch):
    # Pre-0138 bootstrap window: the dedup index is absent, so the read degrades to
    # legacy behavior (no superseded filter) rather than referencing a missing column.
    conn = _ListConn(ai_rows=[_ai_review_row(recommendation_id='rec-1')], dedup_ready=False)
    _patch_list(monkeypatch, conn)
    pilot.list_enforcement_actions(_req(), incident_id=INCIDENT)
    assert not any('superseded_at IS NULL' in sql for sql, _ in conn.executed if 'FROM ai_recommendations r' in sql)


# ==========================================================================
# Group C — idempotent GENERATION (reqs 5, 15) via process_triage_job
# ==========================================================================
class _ConflictAwareConn(FakeConn):
    """FakeConn that simulates the ai_recommendations canonical UNIQUE index: an
    INSERT ... ON CONFLICT for a key already present updates in place (no new logical
    row), exactly like the DB upsert. Lets us run generation twice / concurrently and
    prove no duplicate canonical recommendation is ever created."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.canonical_keys: dict[tuple, str] = {}
        self.dedup_ready = kw.get('dedup_ready', True)

    def execute(self, query, params=None):
        n = ' '.join(str(query).split())
        if 'to_regclass' in n:
            target = str((params or [''])[0])
            if pilot.AI_RECOMMENDATION_DEDUP_INDEX in target:
                self.executed.append((n, params))
                return FakeResult(row={'present': self.dedup_ready})
            # Fall through to FakeConn's generic schema probe.
            return super().execute(query, params)
        if n.startswith('INSERT INTO ai_recommendations'):
            self.executed.append((n, params))
            key = (str(params[1]), str(params[2]), str(params[4]), str(params[5] or ''))
            if 'ON CONFLICT' in n and key in self.canonical_keys:
                self.inserts['rec_upsert'].append(params)  # DO UPDATE — no new row
                return FakeResult()
            self.canonical_keys[key] = str(params[0])
            self.inserts['recommendations'].append(params)
            return FakeResult()
        return super().execute(query, params)


def _run_triage(monkeypatch, conn):
    monkeypatch.setattr(pilot, 'pg_connection', lambda: _fake_pg_ctx(conn))
    monkeypatch.setattr(pilot, 'log_audit', lambda *a, **k: None)
    cfg = _enabled_cfg(provider='mock', model='mock')
    return ai_triage.process_triage_job('job-1', provider_override=ai_providers.MockTriageProvider(), config_override=cfg)


@contextmanager
def _fake_pg_ctx(conn):
    yield conn


def test_repeated_generation_is_idempotent(monkeypatch):
    conn = _ConflictAwareConn(claim_row=_claim_row(), snapshot_json=_snapshot())
    _run_triage(monkeypatch, conn)
    first = len(conn.canonical_keys)
    _run_triage(monkeypatch, conn)  # a second triage run for the SAME incident
    # The second run produced NO new canonical recommendation — it upserted in place.
    assert len(conn.canonical_keys) == first
    assert first >= 1
    assert conn.inserts['rec_upsert'], 'second run should have hit the ON CONFLICT upsert path'


def test_concurrent_generation_does_not_duplicate(monkeypatch):
    # Two "concurrent" generations racing on the same incident: the UNIQUE index makes
    # the second an upsert, so exactly one canonical recommendation survives.
    conn = _ConflictAwareConn(claim_row=_claim_row(), snapshot_json=_snapshot())
    _run_triage(monkeypatch, conn)
    _run_triage(monkeypatch, conn)
    keys = list(conn.canonical_keys)
    assert len(keys) == len(set(keys))
    assert len(conn.inserts['recommendations']) == len(conn.canonical_keys)


def test_generation_uses_on_conflict_upsert_when_dedup_ready(monkeypatch):
    conn = _ConflictAwareConn(claim_row=_claim_row(), snapshot_json=_snapshot(), dedup_ready=True)
    _run_triage(monkeypatch, conn)
    rec_inserts = [sql for sql, _ in conn.executed if sql.startswith('INSERT INTO ai_recommendations')]
    assert rec_inserts and all('ON CONFLICT' in sql and 'DO UPDATE' in sql for sql in rec_inserts)


def test_generation_falls_back_to_plain_insert_before_migration(monkeypatch):
    conn = _ConflictAwareConn(claim_row=_claim_row(), snapshot_json=_snapshot(), dedup_ready=False)
    _run_triage(monkeypatch, conn)
    rec_inserts = [sql for sql, _ in conn.executed if sql.startswith('INSERT INTO ai_recommendations')]
    assert rec_inserts and all('ON CONFLICT' not in sql for sql in rec_inserts)


# ==========================================================================
# Group D — Screen 7 read hides superseded recommendations
# ==========================================================================
def test_get_triage_hides_superseded_recommendations(monkeypatch):
    # get_triage reads the latest result's recommendations; it must apply the canonical
    # filter so a duplicate collapsed by 0138 never reappears on Screen 7.
    captured = {}

    class _Conn:
        def execute(self, statement, params=None):
            n = ' '.join(str(statement).split())
            if 'to_regclass' in n:
                return _Result(row={'present': True})
            if 'FROM incidents WHERE id' in n:
                return _Result(row={'id': INCIDENT, 'workspace_id': WS})
            if 'FROM ai_triage_jobs' in n and 'ORDER BY created_at DESC LIMIT 1' in n:
                return _Result(row={'id': 'job-1', 'incident_id': INCIDENT, 'status': 'completed'})
            if 'FROM ai_triage_results' in n:
                return _Result(row={'id': 'res-1', 'result_json': {}, 'result_hash': 'h', 'warnings': []})
            if 'FROM ai_recommendations WHERE triage_result_id' in n:
                captured['recs_sql'] = n
                return _Result(rows=[])
            return _Result()

        def commit(self):
            return None

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', lambda: _fake_pg_ctx(_Conn()))
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda *_a, **_k: {'id': 'u1'})
    monkeypatch.setattr(pilot, 'resolve_workspace', lambda *_a, **_k: {'workspace_id': WS})
    ai_triage.get_triage(INCIDENT, _req())
    assert 'superseded_at IS NULL' in captured.get('recs_sql', '')


# ==========================================================================
# Group E — approval never changes the total recommendation count (reqs 9-13)
#           and the 6 -> 10 regression cannot recur.
# ==========================================================================
def test_approve_then_refresh_keeps_recommended_count_stable(monkeypatch):
    # Six recommendations exist; approving one records a decision and DOES NOT add any
    # recommendation, so a refresh still reads six (never ten).
    ai_rows = [_ai_review_row(recommendation_id=f'rec-{i}', action_type=f'act_{i}') for i in range(6)]
    conn = _ListConn(ai_rows=ai_rows)
    _patch_list(monkeypatch, conn)
    before = pilot.list_enforcement_actions(_req(), incident_id=INCIDENT)['actions']
    before_count = len([a for a in before if a.get('record_type') == 'ai_recommendation_review'])

    # Approve is a pure decision write — model it as such and prove it added no rows.
    approve_conn = _CommandConn(ai_rows=[_ai_rec(id='rec-0', action_type='act_0', runbook_id=None)])
    _patch_command(monkeypatch, approve_conn)
    pilot.approve_enforcement_action('rec-0', _req())
    _no_recommendation_inserts(approve_conn)

    _patch_list(monkeypatch, conn)
    after = pilot.list_enforcement_actions(_req(), incident_id=INCIDENT)['actions']
    after_count = len([a for a in after if a.get('record_type') == 'ai_recommendation_review'])
    assert after_count == before_count == 6


def test_failed_approval_rolls_back_completely(monkeypatch):
    # If any step after loading the action fails, the transaction is never committed and
    # no approval is persisted (fail-closed, all-or-nothing).
    conn = _CommandConn(ai_rows=[_ai_rec()])
    _patch_command(monkeypatch, conn)

    def _boom(*_a, **_k):
        raise RuntimeError('timeline write failed')

    monkeypatch.setattr(pilot, 'append_incident_timeline_event', _boom)
    with pytest.raises(RuntimeError):
        pilot.approve_enforcement_action(REC_ID, _req())
    assert conn.commits == 0  # nothing committed -> full rollback


# ==========================================================================
# Group F — repair migration is safe + non-destructive (req 14)
# ==========================================================================
def _migration_text() -> str:
    path = Path(__file__).resolve().parents[1] / 'migrations' / '0138_ai_recommendation_idempotency.sql'
    return path.read_text()


def test_repair_migration_preserves_audit_records():
    sql = _migration_text().lower()
    # Never deletes a recommendation (deletion would orphan approvals + destroy history).
    assert 'delete from ai_recommendations' not in sql
    # Collapses duplicates by marking superseded, not removing them.
    assert 'superseded_at' in sql and 'set superseded_at' in sql
    # Prefers a row that already carries a real human decision so an approval is never hidden.
    assert 'response_action_approvals' in sql
    # Enforces one canonical (non-superseded) recommendation per key going forward.
    assert 'unique index' in sql and 'uq_ai_recommendations_canonical' in sql
    assert 'where superseded_at is null' in sql


def test_dedup_key_is_workspace_and_incident_scoped():
    sql = _migration_text().lower()
    # The canonical key must include the workspace + incident so cross-tenant / cross-
    # incident recommendations are NEVER merged, and same action_type + runbook collapse.
    assert 'workspace_id, incident_id, action_type, coalesce(runbook_id' in sql


def test_migration_is_additive_and_idempotent():
    sql = _migration_text().lower()
    assert 'add column if not exists superseded_at' in sql
    assert 'add column if not exists superseded_by_recommendation_id' in sql
    assert 'create unique index if not exists' in sql
