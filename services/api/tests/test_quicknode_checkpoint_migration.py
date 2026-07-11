"""Migration + configuration invariants for the QuickNode live/backfill lanes.

Covers:
  * Migration 0122 preserves the legacy `base` checkpoint AS the backfill cursor and
    NEVER initializes the live checkpoint from the old historical block.
  * The migration is idempotent (ON CONFLICT DO NOTHING), correctly ordered after 0121,
    and discoverable by the migration runner's glob.
  * The canonical env var names the code now reads take precedence over legacy aliases,
    and the new stream-key labels / backfill toggle resolve as documented in .env.example.
"""
from __future__ import annotations

from pathlib import Path

from services.api.app import quicknode_streams as qn

_MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / 'migrations'
_MIGRATION_0122 = _MIGRATIONS_DIR / '0122_quicknode_base_checkpoint_to_backfill.sql'


def test_migration_0122_exists_and_orders_after_0121():
    names = sorted(p.name for p in _MIGRATIONS_DIR.glob('*.sql'))
    assert '0122_quicknode_base_checkpoint_to_backfill.sql' in names
    assert names.index('0122_quicknode_base_checkpoint_to_backfill.sql') > \
        names.index('0121_quicknode_live_backfill_checkpoints.sql')


def test_migration_0122_preserves_base_as_backfill_and_is_idempotent():
    sql = _MIGRATION_0122.read_text().lower()
    # Copies the base cursor into the backfill lane...
    assert "'quicknode:base:backfill'" in sql
    assert "where base.stream_key = 'base'" in sql
    # ...idempotently...
    assert 'on conflict (stream_key) do nothing' in sql
    # ...and only when base actually has progress.
    assert 'last_processed_block is not null' in sql


def test_migration_0122_never_seeds_live_checkpoint():
    """The live checkpoint must initialize from the current chain head via startup
    logic — NEVER from the old historical block. The migration must not insert a
    quicknode:base:live row."""
    sql = _MIGRATION_0122.read_text()
    insert_section = sql.split('INSERT INTO', 1)[1]
    assert 'quicknode:base:live' not in insert_section


def test_migration_runner_would_load_0122():
    # Mirror pilot._run_migrations_once's discovery: sorted glob of *.sql.
    discovered = [p.name for p in sorted(_MIGRATIONS_DIR.glob('*.sql'))]
    assert '0122_quicknode_base_checkpoint_to_backfill.sql' in discovered


# ---------------------------------------------------------------------------
# Configuration variables (genuinely read by the code)
# ---------------------------------------------------------------------------

def test_confirmation_blocks_canonical_name_wins_over_legacy(monkeypatch):
    monkeypatch.setenv('QUICKNODE_LIVE_CONFIRMATION_BLOCKS', '7')
    monkeypatch.setenv('QUICKNODE_LIVE_CONFIRMATIONS', '2')
    assert qn.live_confirmations() == 7
    monkeypatch.delenv('QUICKNODE_LIVE_CONFIRMATION_BLOCKS', raising=False)
    assert qn.live_confirmations() == 2  # legacy alias still honored


def test_max_lag_blocks_canonical_name_wins_over_legacy(monkeypatch):
    monkeypatch.setenv('QUICKNODE_LIVE_MAX_LAG_BLOCKS', '4')
    monkeypatch.setenv('QUICKNODE_LIVE_LAG_THRESHOLD_BLOCKS', '10')
    assert qn.live_lag_threshold_blocks() == 4
    monkeypatch.delenv('QUICKNODE_LIVE_MAX_LAG_BLOCKS', raising=False)
    assert qn.live_lag_threshold_blocks() == 10


def test_stream_key_labels_default_and_override(monkeypatch):
    monkeypatch.delenv('QUICKNODE_LIVE_STREAM_KEY', raising=False)
    monkeypatch.delenv('QUICKNODE_BACKFILL_STREAM_KEY', raising=False)
    assert qn.quicknode_live_stream_key() == 'base-live'
    assert qn.quicknode_backfill_stream_key() == 'base-backfill'
    monkeypatch.setenv('QUICKNODE_LIVE_STREAM_KEY', 'base-tip')
    assert qn.quicknode_live_stream_key() == 'base-tip'


def test_backfill_enabled_defaults_true_and_toggles_off(monkeypatch):
    monkeypatch.delenv('QUICKNODE_BACKFILL_ENABLED', raising=False)
    assert qn.quicknode_backfill_enabled() is True
    monkeypatch.setenv('QUICKNODE_BACKFILL_ENABLED', 'false')
    assert qn.quicknode_backfill_enabled() is False
    monkeypatch.setenv('QUICKNODE_BACKFILL_ENABLED', 'true')
    assert qn.quicknode_backfill_enabled() is True
