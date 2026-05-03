from pathlib import Path


def test_web_detector_label_map_includes_canonical_codes():
    content = Path('/workspace/decoda-rwa-guard/apps/web/app/threat/detector-labels.ts').read_text(encoding='utf-8')
    for code in [
        'oracle_nav_divergence',
        'proof_of_reserve_stale',
        'unauthorized_mint_burn',
        'abnormal_redemption_activity',
        'custody_wallet_movement_anomaly',
        'compliance_exposure',
        'monitoring_coverage_gap',
    ]:
        assert code in content
    assert 'detectorKindLabel' in content
