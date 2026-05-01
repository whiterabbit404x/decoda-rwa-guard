from pathlib import Path


def test_architecture_sections_route_contract_conformance() -> None:
    main_source = Path('services/api/app/main.py').read_text(encoding='utf-8')

    required_sections = {
        'onboarding': ["@app.get('/onboarding/state'", "@app.patch('/onboarding/state'"],
        'assets': ["@app.get('/assets'", "@app.post('/assets'"],
        'monitoring_sources': ["@app.get('/monitoring/systems'", "@app.post('/monitoring/systems/reconcile'"],
        'threat_monitoring': ["@app.get('/threat/dashboard'", "@app.post('/threat/analyze/contract'"],
        'alerts': ["@app.get('/alerts'", "@app.get('/alerts/{alert_id}'"],
        'incidents': ["@app.get('/incidents'", "@app.get('/incidents/{incident_id}/timeline'"],
        'response_actions': ["@app.get('/response/actions'", "@app.post('/response/actions/{action_id}/execute'"],
        'integrations': ["@app.get('/integrations/webhooks'", "@app.post('/integrations/webhooks'"],
    }

    for section, needles in required_sections.items():
        for needle in needles:
            assert needle in main_source, f"missing {section} contract route marker: {needle}"


def test_architecture_sections_ops_dashboard_contract_fields_present() -> None:
    main_source = Path('services/api/app/main.py').read_text(encoding='utf-8')

    expected_fields = [
        "'dashboard': dashboard()",
        "'risk_dashboard': risk_dashboard()",
        "'threat_dashboard': threat_dashboard()",
        "'compliance_dashboard': compliance_dashboard()",
        "'resilience_dashboard': resilience_dashboard()",
        "'workspace_monitoring_summary':",
        "'background_loop_health':",
    ]

    for field_name in expected_fields:
        assert field_name in main_source, f"missing architecture contract field: {field_name}"
