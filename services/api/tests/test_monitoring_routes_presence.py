from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_monitoring_routes_registered() -> None:
    content = (REPO_ROOT / 'services/api/app/main.py').read_text(encoding='utf-8')
    assert "@app.get('/monitoring/targets'" in content
    assert "@app.patch('/monitoring/targets/{target_id}'" in content
    assert "@app.get('/monitoring/systems'" in content
    assert "@app.post('/monitoring/systems/reconcile'" in content
    assert "@app.get('/monitoring/workspace-debug'" in content
    assert "@app.post('/monitoring/systems'" in content
    assert "@app.patch('/monitoring/systems/{system_id}'" in content
    assert "@app.delete('/monitoring/systems/{system_id}'" in content
    assert "@app.post('/monitoring/run-once/{target_id}'" in content
    assert "@app.get('/ops/monitoring/health'" in content
    assert "@app.get('/ops/monitoring/runtime-status'" in content
    assert "@app.get('/ops/monitoring/evidence'" in content
    assert "@app.get('/ops/monitoring/heartbeats'" in content
    assert "@app.get('/incidents'" in content
    assert "@app.get('/response/actions'" in content
    assert "@app.post('/response/actions'" in content
    assert "@app.post('/response/actions/{action_id}/execute'" in content
    assert "@app.patch('/incidents/{incident_id}'" in content
    assert "@app.post('/alerts/{alert_id}/acknowledge'" in content
    assert "@app.post('/alerts/{alert_id}/resolve'" in content
    assert "@app.post('/alerts/{alert_id}/escalate'" in content
    assert "@app.get('/onboarding/progress'" in content
    assert "@app.get('/workspaces/current'" in content
    assert "@app.post('/targets/{target_id}/enable'" in content
    assert "@app.post('/targets/{target_id}/disable'" in content
