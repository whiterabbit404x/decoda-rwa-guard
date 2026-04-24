from services.api.app import pilot


def test_monitoring_proof_chain_correlation_id_is_stable_per_day_bucket() -> None:
    first = pilot._monitoring_proof_chain_correlation_id(
        workspace_id='ws-1',
        monitored_system_id='sys-1',
        date_bucket='2026-04-24',
    )
    second = pilot._monitoring_proof_chain_correlation_id(
        workspace_id='ws-1',
        monitored_system_id='sys-1',
        date_bucket='2026-04-24',
    )
    next_day = pilot._monitoring_proof_chain_correlation_id(
        workspace_id='ws-1',
        monitored_system_id='sys-1',
        date_bucket='2026-04-25',
    )

    assert first == second
    assert first != next_day
