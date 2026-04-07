from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_monitoring_routes_registered() -> None:
    content = (REPO_ROOT / 'services/api/app/main.py').read_text(encoding='utf-8')
    assert "@app.get('/monitoring/targets'" in content
    assert "@app.patch('/monitoring/targets/{target_id}'" in content
    assert "@app.post('/monitoring/run-once/{target_id}'" in content
    assert "@app.get('/ops/monitoring/health'" in content
    assert "@app.get('/incidents'" in content
    assert "@app.patch('/incidents/{incident_id}'" in content
    assert "@app.get('/onboarding/progress'" in content
    assert "@app.get('/workspaces/current'" in content
    assert "@app.post('/targets/{target_id}/enable'" in content
    assert "@app.post('/targets/{target_id}/disable'" in content
