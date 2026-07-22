"""
Schema-accurate tests for migration 0129 (restore Datto USDC to scheduled polling).

Why this file exists
--------------------
0129 shipped an ``UPDATE monitored_systems SET ... updated_at = NOW()`` even though
``monitored_systems`` has **no** ``updated_at`` column (it was created in migration 0034
without one and none was ever added — migration 0103 documents the same fact). The deploy
aborted in production with::

    psycopg.errors.UndefinedColumn:
    column "updated_at" of relation "monitored_systems" does not exist

The previous 0129 tests only string-matched the SQL text, so they never noticed a column
that does not exist. This module closes that gap two ways:

Part A — schema-derivation guard (always runs, no database required)
    The column set of ``monitored_systems`` / ``targets`` / ``monitoring_configs`` is DERIVED
    by parsing the real migration files 0001-0128 (CREATE TABLE / ADD COLUMN / DROP COLUMN /
    RENAME COLUMN). Every column that 0129 references (UPDATE SET targets, UPDATE WHERE keys,
    and the diagnostic ``SELECT ... INTO`` lists) is asserted to exist in that derived schema.
    This is what would have caught the bug in CI, and it fails again the moment any future
    edit references a column the migrations never create.

Part B — real-PostgreSQL execution harness (opt-in; marked ``integration``)
    Applies the real migrations 0001-0128 to a throwaway PostgreSQL database, then executes
    0129 across the six required scenarios (first apply, idempotent re-apply, disabled target,
    soft-deleted target, already-healthy no-op, and a unique-key collision) using the SQL in
    ``migration_harness/``. It is skipped unless ``DECODA_MIGRATION_TEST_DSN`` is set and
    ``psql`` is available, so the stubbed unit-test CI is unaffected.

Acceptance items covered (from the task):
    1 0129 executes when monitored_systems lacks updated_at   -> Part A + Part B scenario 1
    2 the Datto monitored system is repaired                  -> Part B scenario 1
    3 the Datto target is repaired                            -> Part B scenario 1
    4 the existing monitoring configuration is preserved      -> Part B scenarios 1/2
    5 a second execution is idempotent                        -> Part B scenario 2
    6 no duplicate target/system/config is created            -> Part B scenarios 1/2/6
    7 a unique-index collision does not abort the migration   -> Part B scenario 6
    8 every column referenced by 0129 exists in the schema    -> Part A
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_API_ROOT = Path(__file__).resolve().parents[1]
_MIGRATIONS_DIR = _API_ROOT / 'migrations'
_M0129 = _MIGRATIONS_DIR / '0129_restore_datto_usdc_scheduled_polling.sql'
_HARNESS_DIR = Path(__file__).resolve().parent / 'migration_harness'

# Tables 0129 touches, plus the ordered acceptance ids.
_TABLES = ('monitored_systems', 'targets', 'monitoring_configs')

# Authoritative column list captured by applying migrations 0001-0128 to a real
# PostgreSQL 16 instance and reading information_schema. Anchored here so the parser
# below is proven correct against production reality — and so the absence of updated_at
# is pinned as an explicit fact, not an inference.
_REAL_MONITORED_SYSTEMS_COLUMNS = {
    'id', 'workspace_id', 'asset_id', 'target_id', 'chain', 'status', 'last_heartbeat',
    'created_at', 'is_enabled', 'runtime_status', 'last_error_text', 'last_event_at',
    'freshness_status', 'confidence_status', 'coverage_reason', 'last_coverage_telemetry_at',
}


# ---------------------------------------------------------------------------
# Minimal, scoped SQL parsing helpers (good enough for these DDL shapes).
# ---------------------------------------------------------------------------
def _strip_line_comments(sql: str) -> str:
    return '\n'.join(re.sub(r'--.*$', '', line) for line in sql.splitlines())


def _paren_body(text: str, open_idx: int) -> str:
    depth = 0
    for i in range(open_idx, len(text)):
        ch = text[i]
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
            if depth == 0:
                return text[open_idx + 1:i]
    raise ValueError('unbalanced parentheses')


def _split_top_commas(body: str) -> list[str]:
    parts, cur, depth = [], [], 0
    for ch in body:
        if ch == '(':
            depth += 1
            cur.append(ch)
        elif ch == ')':
            depth -= 1
            cur.append(ch)
        elif ch == ',' and depth == 0:
            parts.append(''.join(cur))
            cur = []
        else:
            cur.append(ch)
    if ''.join(cur).strip():
        parts.append(''.join(cur))
    return parts


def _until_top_semicolon(text: str, start: int) -> str:
    depth = 0
    for i in range(start, len(text)):
        ch = text[i]
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
        elif ch == ';' and depth == 0:
            return text[start:i]
    return text[start:]


_CONSTRAINT_KEYWORDS = {'CONSTRAINT', 'PRIMARY', 'UNIQUE', 'FOREIGN', 'CHECK', 'EXCLUDE', 'LIKE'}


def derive_columns(sql_text: str, table: str) -> list[str]:
    """Derive the column list of ``table`` from concatenated migration SQL.

    Handles CREATE TABLE [IF NOT EXISTS], ALTER TABLE ADD COLUMN [IF NOT EXISTS],
    DROP COLUMN [IF EXISTS] and RENAME COLUMN ... TO ...  — the operations the real
    migrations use to shape these tables.
    """
    sql = _strip_line_comments(sql_text)
    cols: list[str] = []
    have: set[str] = set()

    def add(name: str) -> None:
        n = name.strip().strip('"').lower()
        if n and n not in have:
            have.add(n)
            cols.append(n)

    def drop(name: str) -> None:
        n = name.strip().strip('"').lower()
        if n in have:
            have.discard(n)
            cols.remove(n)

    tbl = re.escape(table)

    # CREATE TABLE bodies
    for m in re.finditer(
        r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?"?' + tbl + r'"?\s*\(', sql_text, re.I
    ):
        body = _paren_body(sql_text, sql_text.index('(', m.end() - 1))
        for item in _split_top_commas(_strip_line_comments(body)):
            item = item.strip()
            if not item:
                continue
            first = item.split(None, 1)[0]
            if first.strip('"').upper() in _CONSTRAINT_KEYWORDS:
                continue
            add(first)

    # ALTER TABLE statements
    for m in re.finditer(r'ALTER\s+TABLE\s+(?:ONLY\s+)?"?' + tbl + r'"?\b', sql, re.I):
        stmt = _until_top_semicolon(sql, m.end())
        for a in re.finditer(r'ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?"?(\w+)"?', stmt, re.I):
            add(a.group(1))
        for d in re.finditer(r'DROP\s+COLUMN\s+(?:IF\s+EXISTS\s+)?"?(\w+)"?', stmt, re.I):
            drop(d.group(1))
        for r in re.finditer(r'RENAME\s+COLUMN\s+"?(\w+)"?\s+TO\s+"?(\w+)"?', stmt, re.I):
            drop(r.group(1))
            add(r.group(2))

    return cols


def _migrations_through_0128() -> str:
    files = sorted(p for p in _MIGRATIONS_DIR.glob('*.sql') if p.name < '0129_')
    return '\n'.join(p.read_text() for p in files)


# ---------------------------------------------------------------------------
# Reference extraction from migration 0129 (which columns of which table it uses).
# ---------------------------------------------------------------------------
def _column_references(sql_text: str) -> list[tuple[str, str, str]]:
    """Return (table, column, context) for every column 0129 references on the
    three tables under test: UPDATE SET targets, UPDATE WHERE equality keys, and the
    diagnostic ``SELECT <cols> INTO _v FROM <table>`` lists."""
    sql = _strip_line_comments(sql_text)
    refs: list[tuple[str, str, str]] = []

    # SELECT <cols> INTO _v FROM <table>
    for m in re.finditer(r'SELECT\s+(.*?)\s+INTO\s+\w+\s+FROM\s+(\w+)', sql, re.I | re.S):
        table = m.group(2).lower()
        if table not in _TABLES:
            continue
        for raw in _split_top_commas(m.group(1)):
            tok = raw.strip().split()[0] if raw.strip() else ''
            if re.fullmatch(r'\w+', tok):
                refs.append((table, tok.lower(), 'select_into'))

    # UPDATE <table> SET <...> WHERE <...>;
    for m in re.finditer(r'\bUPDATE\s+(\w+)\s+SET\b', sql, re.I):
        table = m.group(1).lower()
        if table not in _TABLES:
            continue
        i = m.end()
        depth, where_start, end = 0, None, len(sql)
        j = i
        while j < len(sql):
            ch = sql[j]
            if ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
            elif depth == 0:
                if where_start is None and sql[j:j + 5].upper() == 'WHERE' \
                        and (j + 5 >= len(sql) or not sql[j + 5].isalnum()):
                    where_start = j
                elif ch == ';':
                    end = j
                    break
            j += 1
        set_body = sql[i:where_start] if where_start is not None else sql[i:end]
        where_body = sql[where_start + 5:end] if where_start is not None else ''
        for part in _split_top_commas(set_body):
            d = 0
            for k, ch in enumerate(part):
                if ch == '(':
                    d += 1
                elif ch == ')':
                    d -= 1
                elif ch == '=' and d == 0:
                    lhs = part[:k].strip().strip('"')
                    if re.fullmatch(r'\w+', lhs):
                        refs.append((table, lhs.lower(), 'update_set'))
                    break
        for col in re.findall(r'(\w+)\s*=\s*\'', where_body):
            refs.append((table, col.lower(), 'update_where'))

    return refs


# ---------------------------------------------------------------------------
# Part A — schema-derivation guard (always runs, no database)
# ---------------------------------------------------------------------------
def test_migration_file_exists_and_orders_after_0128():
    names = sorted(p.name for p in _MIGRATIONS_DIR.glob('*.sql'))
    assert _M0129.name in names
    assert names.index(_M0129.name) > names.index('0128_backfill_missing_direct_monitoring_configs.sql')


def test_parser_matches_real_postgres_for_monitored_systems():
    """The derivation parser reproduces the exact production monitored_systems schema
    (captured from applying 0001-0128 to real PostgreSQL 16)."""
    derived = set(derive_columns(_migrations_through_0128(), 'monitored_systems'))
    assert derived == _REAL_MONITORED_SYSTEMS_COLUMNS, (
        f'parser drift: only-in-derived={derived - _REAL_MONITORED_SYSTEMS_COLUMNS}, '
        f'missing={_REAL_MONITORED_SYSTEMS_COLUMNS - derived}'
    )


def test_monitored_systems_has_no_updated_at_column():
    """The crux: monitored_systems never gains an updated_at column across 0001-0128."""
    cols = derive_columns(_migrations_through_0128(), 'monitored_systems')
    assert 'updated_at' not in cols, 'monitored_systems must NOT have an updated_at column'


def test_targets_and_configs_do_have_updated_at_column():
    """The sibling tables DO have updated_at, so stamping them in 0129 is valid."""
    base = _migrations_through_0128()
    assert 'updated_at' in derive_columns(base, 'targets')
    assert 'updated_at' in derive_columns(base, 'monitoring_configs')


def test_every_column_referenced_by_0129_exists_in_derived_schema():
    """Acceptance item 8. Each column 0129 reads or writes on the three tables must exist
    in the schema derived from migrations 0001-0128. This is the assertion that fails on the
    original migration (monitored_systems.updated_at) and passes on the corrected one."""
    base = _migrations_through_0128()
    derived = {t: set(derive_columns(base, t)) for t in _TABLES}
    refs = _column_references(_M0129.read_text())
    # Guard: the extractor actually found the write surface (3 UPDATEs + the DO-block SELECTs).
    assert any(ctx == 'update_set' for _, _, ctx in refs)
    assert {t for t, _, _ in refs} == set(_TABLES)
    missing = [(t, c, ctx) for (t, c, ctx) in refs if c not in derived[t]]
    assert not missing, f'migration 0129 references columns that do not exist: {missing}'


def test_monitored_systems_update_does_not_set_updated_at():
    """Belt-and-braces on the exact defect: the monitored_systems UPDATE must not assign
    updated_at (nor may the diagnostic SELECT read it)."""
    refs = _column_references(_M0129.read_text())
    ms_cols = {c for (t, c, _) in refs if t == 'monitored_systems'}
    assert 'updated_at' not in ms_cols
    # It still repairs the columns that DO exist.
    assert {'is_enabled', 'runtime_status'} <= ms_cols


# --- retained static invariants (idempotent / scoped / crash-safe / diagnostic) ----------
def _executable_0129() -> str:
    body = '\n'.join(l for l in _M0129.read_text().splitlines() if not l.lstrip().startswith('--'))
    return ' '.join(body.split())


def test_migration_is_duplicate_free_no_insert():
    up = _executable_0129().upper()
    assert 'INSERT INTO' not in up and 'INSERT ' not in up
    assert 'ENABLED = TRUE' in up and 'MONITORING_ENABLED = TRUE' in up
    assert 'IS_ACTIVE = TRUE' in up and "PROVIDER_TYPE = 'EVM_RPC'" in up
    assert 'GREATEST(COALESCE(MONITORING_INTERVAL_SECONDS, 900), 900)' in up


def test_migration_is_workspace_scoped_per_update():
    ex = _executable_0129()
    ws = '4fffd3f9-d55f-456f-8a7e-8b9ed2083721'
    update_count = ex.upper().count('UPDATE ')
    assert update_count >= 3
    assert ex.count(ws) >= update_count, 'every UPDATE must pin the Datto workspace_id'


def test_migration_undelete_is_crash_safe_under_unique_index():
    up = _executable_0129().upper()
    assert 'DELETED_AT = CASE' in up and 'NOT EXISTS' in up
    for col in ('WORKSPACE_ID', 'ASSET_ID', 'NAME', 'TARGET_TYPE'):
        assert col in up
    assert 'IS NOT DISTINCT FROM' in up


def test_migration_is_diagnostic_and_transaction_safe():
    up = _executable_0129().upper()
    assert 'RAISE NOTICE' in up
    assert 'CONCURRENTLY' not in up and 'VACUUM' not in up


# ---------------------------------------------------------------------------
# Part B — real-PostgreSQL execution harness (opt-in; integration)
# ---------------------------------------------------------------------------
_DSN = os.environ.get('DECODA_MIGRATION_TEST_DSN')
_PSQL = shutil.which('psql')
_needs_pg = pytest.mark.skipif(
    not (_DSN and _PSQL),
    reason='set DECODA_MIGRATION_TEST_DSN (a disposable/empty PostgreSQL database) and have '
           'psql on PATH to run the real-schema 0129 execution harness',
)


def _psql(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        [_PSQL, _DSN, *args], capture_output=True, text=True, timeout=600,
    )
    if check and proc.returncode != 0:
        raise AssertionError(f'psql {args} failed rc={proc.returncode}\n{proc.stdout}\n{proc.stderr}')
    return proc


@pytest.mark.integration
@_needs_pg
def test_migration_0129_executes_across_all_scenarios_on_real_postgres():
    """Build the production schema from the real migrations 0001-0128, then run 0129 across
    all six scenarios. Any UndefinedColumn/UndefinedTable, unique violation, aborted
    transaction, or failed post-condition makes psql (ON_ERROR_STOP=1) exit non-zero.

    Expects DECODA_MIGRATION_TEST_DSN to point at a disposable/empty database.
    """
    for f in sorted(p for p in _MIGRATIONS_DIR.glob('*.sql') if p.name < '0129_'):
        _psql(['-v', 'ON_ERROR_STOP=1', '--single-transaction', '-q', '-f', str(f)])
    _psql(['-v', 'ON_ERROR_STOP=1', '-q', '-f', str(_HARNESS_DIR / 'seed_datto_shared.sql')])
    res = _psql(
        ['-v', 'ON_ERROR_STOP=1', '-v', f'MIG={_M0129}', '-f', str(_HARNESS_DIR / 'scenarios_0129.sql')],
        check=False,
    )
    combined = res.stdout + res.stderr
    assert res.returncode == 0, combined
    for n in range(1, 7):
        assert f'SCENARIO {n} PASS' in combined, f'scenario {n} did not pass:\n{combined}'
