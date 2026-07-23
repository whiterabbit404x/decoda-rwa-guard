"""Risk Trend: real snapshots only — no fabricated dates, no forward-fill.

Proves the seven-day trend never invents history:

  * missing days are absent, not synthesized;
  * the current/previous score is never carried across a gap;
  * multiple snapshots from one UTC day collapse to one real point (latest of the
    day) with its real captured_at, never shown as several days;
  * availability/partial coverage are derived from real daily points only.
"""

from __future__ import annotations

from services.api.app import dashboard_summary as ds


def _snap(ts, *, risk=10, health=90, alerts=0, incidents=0):
    return {
        'captured_at': ts, 'risk_score': risk, 'health_score': health,
        'active_alert_count': alerts, 'open_incident_count': incidents,
    }


def test_empty_and_single_snapshot_are_not_available():
    assert ds.build_risk_trend([]) == []
    meta_empty = ds.compute_trend_meta([])
    assert meta_empty == {'available': False, 'partial': False, 'days_covered': 0}

    one = ds.build_risk_trend([_snap('2026-07-23T01:00:00+00:00')])
    assert len(one) == 1
    meta_one = ds.compute_trend_meta(one)
    assert meta_one['available'] is False   # fewer than two real points
    assert meta_one['partial'] is False


def test_same_day_snapshots_collapse_to_one_real_point():
    snaps = [
        _snap('2026-07-22T02:00:00+00:00', risk=20),
        _snap('2026-07-22T23:30:00+00:00', risk=35),  # same UTC day, later
        _snap('2026-07-23T08:00:00+00:00', risk=50),
    ]
    trend = ds.build_risk_trend(snaps)
    # Two real days -> two points (not three); same-day captures aggregate to the
    # latest snapshot of the day, keeping its real timestamp.
    assert len(trend) == 2
    assert trend[0]['captured_date'] == '2026-07-22'
    assert trend[0]['risk_score'] == 35
    assert trend[0]['captured_at'] == '2026-07-22T23:30:00+00:00'
    assert trend[1]['captured_date'] == '2026-07-23'
    assert trend[1]['risk_score'] == 50


def test_missing_days_are_not_fabricated():
    # A gap (2026-07-21) between two real days must NOT appear as a point.
    snaps = [_snap('2026-07-20T12:00:00+00:00', risk=10), _snap('2026-07-22T12:00:00+00:00', risk=40)]
    trend = ds.build_risk_trend(snaps)
    dates = [p['captured_date'] for p in trend]
    assert dates == ['2026-07-20', '2026-07-22']
    assert '2026-07-21' not in dates
    # Every plotted point is backed by a real captured_at timestamp.
    assert all(p['captured_at'] for p in trend)


def test_no_forward_fill_of_scores_across_gaps():
    snaps = [_snap('2026-07-21T12:00:00+00:00', risk=25), _snap('2026-07-23T12:00:00+00:00', risk=70)]
    trend = ds.build_risk_trend(snaps)
    scores_by_day = {p['captured_date']: p['risk_score'] for p in trend}
    # The missing 2026-07-22 is never assigned 25 or 70.
    assert scores_by_day == {'2026-07-21': 25, '2026-07-23': 70}


def test_partial_when_fewer_than_seven_days_covered():
    snaps = [_snap(f'2026-07-2{d}T12:00:00+00:00') for d in (1, 2, 3)]
    trend = ds.build_risk_trend(snaps)
    meta = ds.compute_trend_meta(trend, days=7)
    assert meta['available'] is True
    assert meta['partial'] is True
    assert meta['days_covered'] == 3


def test_full_week_is_not_partial():
    days = ['2026-07-17', '2026-07-18', '2026-07-19', '2026-07-20', '2026-07-21', '2026-07-22', '2026-07-23']
    snaps = [_snap(f'{d}T12:00:00+00:00') for d in days]
    trend = ds.build_risk_trend(snaps)
    meta = ds.compute_trend_meta(trend, days=7)
    assert meta['days_covered'] == 7
    assert meta['available'] is True
    assert meta['partial'] is False


def test_rows_without_timestamps_are_dropped_not_dated():
    snaps = [_snap('2026-07-22T12:00:00+00:00'), _snap(None), {'risk_score': 5}]
    trend = ds.build_risk_trend(snaps)
    assert len(trend) == 1
    assert trend[0]['captured_date'] == '2026-07-22'
