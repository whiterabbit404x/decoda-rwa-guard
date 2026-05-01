from pathlib import Path


def test_web_detector_label_map_includes_canonical_codes():
    content = Path('/workspace/decoda-rwa-guard/apps/web/app/threat/detector-labels.ts').read_text(encoding='utf-8')
    for code in [
        'oracle_divergence',
        'reserve_mismatch',
        'unauthorized_mint_burn',
        'abnormal_redemption_activity',
        'contract_upgrade_anomaly',
        'custody_transfer_anomaly',
        'compliance_exposure',
        'monitoring_coverage_gap',
    ]:
        assert code in content
    assert 'detectorKindLabel' in content
