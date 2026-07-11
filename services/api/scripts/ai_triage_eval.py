"""Optional manual evaluation of the AI triage agent against a REAL provider.

Unit/integration tests always use the deterministic mock provider. This command
lets an operator exercise the configured live provider against the same
deterministic RWA fixtures and see whether each response passes grounding/schema
validation. It never touches the database and executes no action.

Usage:
    AI_TRIAGE_ENABLED=true AI_PROVIDER=anthropic AI_MODEL_TRIAGE=claude-opus-4-8 \
        AI_API_KEY=sk-... python -m services.api.scripts.ai_triage_eval

Exit code is non-zero if any fixture fails validation, so it can gate a manual
pre-enablement check.
"""
from __future__ import annotations

import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from services.api.app import ai_triage, ai_providers  # noqa: E402


def _fixture_snapshots() -> dict:
    base = {
        'schema_version': '1.0', 'workspace_id': 'ws-eval', 'incident_id': 'inc-eval',
        'alert': {'alert_id': 'alert-eval', 'severity': 'high', 'created_at': '2026-07-11T00:00:00+00:00', 'rule_id': 'wallet_transfer'},
        'rule': {'rule_id': 'wallet_transfer', 'name': 'Wallet transfer', 'description': 'Monitored wallet transfer detected.', 'conditions': {}, 'version': '1'},
        'target': {'target_id': 'tgt-eval', 'asset_id': None, 'chain_id': 8453, 'address': '0xtarget', 'asset_type': 'wallet'},
        'telemetry': [{
            'telemetry_id': 'tel-1', 'event_type': 'wallet_transfer_detected', 'detected_by': 'quicknode_stream',
            'tx_hash': '0xdead', 'from': '0xfrom', 'to': '0xto', 'value': '100', 'block_number': 123,
            'chain_id': 8453, 'observed_at': '2026-07-11T00:00:00+00:00', 'ingested_at': '2026-07-11T00:00:01+00:00',
            'evidence_source': 'live_provider',
        }],
        'provider_observations': [], 'policies': [{'policy_version': '1.0'}],
        'available_runbooks': [{'runbook_id': rid, 'action_type': m['action_type'], 'risk_level': m['risk_level'], 'name': m['name']} for rid, m in ai_triage.RUNBOOK_CATALOG.items()],
        'audit_references': [],
    }
    return {'normal_transfer': base}


def main() -> int:
    config = ai_triage.triage_config()
    for warning in ai_triage.configuration_warnings(config):
        print(f'[warn] {warning}')
    provider = ai_providers.get_triage_provider(config['provider'])
    print(f"Provider: {getattr(provider, 'name', 'unknown')}  model: {config['model'] or '(default)'}")

    failures = 0
    for name, snapshot in _fixture_snapshots().items():
        prompt = ai_triage.build_prompt(snapshot, ai_triage.AGENT_POLICY, prompt_version=config['prompt_version'])
        try:
            raw = provider.analyze(prompt=prompt, model=config['model'], timeout_seconds=config['request_timeout_seconds'], max_output_tokens=config['max_output_tokens'])
            validated = ai_triage.validate_triage_output(raw.raw_text, snapshot, ai_triage.AGENT_POLICY)
            print(f'[pass] {name}: {len(validated["result"].get("citations") or [])} citations, {len(validated["warnings"])} warnings')
        except ai_providers.TriageProviderError as exc:
            failures += 1
            print(f'[fail] {name}: provider error {exc.error_code}')
        except ai_triage.TriageValidationError as exc:
            failures += 1
            print(f'[fail] {name}: validation {exc.error_code} - {exc.detail}')
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
