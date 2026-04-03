from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_new_live_workflow_routes_exist() -> None:
    source = (REPO_ROOT / 'services/api/app/main.py').read_text(encoding='utf-8')
    assert "/exports/{export_id}/download" in source
    assert "/team/seats" in source
    assert "/workspace/invitations/{invitation_id}" in source
    assert "/workspace/invitations/{invitation_id}/resend" in source
    assert "/findings/{finding_id}/decision" in source
    assert "/findings/{finding_id}/actions" in source
    assert "/actions/{action_id}" in source
    assert "/integrations/webhooks/{webhook_id}/rotate-secret" in source
    assert "/integrations/slack/{integration_id}/test" in source
    assert "/integrations/routing/{channel_type}" in source
    assert "/billing/webhooks/paddle" in source


def test_export_generation_is_not_placeholder_complete() -> None:
    source = (REPO_ROOT / 'services/api/app/pilot.py').read_text(encoding='utf-8')
    assert "VALUES (%s, %s, %s, %s, %s, %s::jsonb, 'queued', %s, %s, %s)" in source
    assert "def _generate_export_artifact" in source
    assert "status = 'completed'" in source


def test_protected_pages_use_authenticated_client_fetch_flow() -> None:
    alerts_page = (REPO_ROOT / 'apps/web/app/(product)/alerts/page.tsx').read_text(encoding='utf-8')
    integrations_page = (REPO_ROOT / 'apps/web/app/(product)/integrations/page.tsx').read_text(encoding='utf-8')
    templates_page = (REPO_ROOT / 'apps/web/app/(product)/templates/page.tsx').read_text(encoding='utf-8')
    settings_page = (REPO_ROOT / 'apps/web/app/(product)/settings/page.tsx').read_text(encoding='utf-8')
    alerts_client = (REPO_ROOT / 'apps/web/app/(product)/alerts-page-client.tsx').read_text(encoding='utf-8')
    integrations_client = (REPO_ROOT / 'apps/web/app/(product)/integrations-page-client.tsx').read_text(encoding='utf-8')
    templates_client = (REPO_ROOT / 'apps/web/app/(product)/templates-page-client.tsx').read_text(encoding='utf-8')
    settings_client = (REPO_ROOT / 'apps/web/app/settings-page-client.tsx').read_text(encoding='utf-8')

    assert 'fetch(`${apiUrl}/alerts`' not in alerts_page
    assert 'fetch(`${data.apiUrl}/integrations/webhooks`' not in integrations_page
    assert 'fetch(`${data.apiUrl}/integrations/slack`' not in integrations_page
    assert 'fetch(`${data.apiUrl}/templates`' not in templates_page
    assert 'fetch(`${data.apiUrl}/workspace/members`' not in settings_page
    assert 'authHeaders()' in alerts_client
    assert 'authHeaders()' in integrations_client
    assert 'authHeaders()' in templates_client
    assert 'authHeaders()' in settings_client
