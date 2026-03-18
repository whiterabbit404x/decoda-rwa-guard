from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from phase1_local.dev_support import REPO_ROOT, utc_now

DEFAULT_LEDGER_PATH = REPO_ROOT / '.data' / 'compliance_governance_actions.json'
DEFAULT_POLICY_PATH = REPO_ROOT / '.data' / 'compliance_policy_state.json'
DEFAULT_POLICY_STATE = {
    'allowlisted_wallets': [
        '0xaaa0000000000000000000000000000000000101',
        '0xbbb0000000000000000000000000000000000202',
    ],
    'blocklisted_wallets': ['0xblocked000000000000000000000000000000003'],
    'frozen_wallets': [],
    'review_required_wallets': ['0xreview000000000000000000000000000000004'],
    'paused_assets': [],
    'approved_cloud_regions': ['us-east', 'us-central', 'eu-west'],
    'friendly_regions': ['us-east', 'us-central', 'eu-west', 'sg-gov'],
    'restricted_regions': ['cn-north', 'ru-central', 'ir-gov'],
}


class ComplianceStore:
    def __init__(self, ledger_path: Path | None = None, policy_path: Path | None = None) -> None:
        self.ledger_path = ledger_path or self._resolve_path(os.getenv('COMPLIANCE_LEDGER_PATH'), DEFAULT_LEDGER_PATH)
        self.policy_path = policy_path or self._resolve_path(os.getenv('COMPLIANCE_POLICY_PATH'), DEFAULT_POLICY_PATH)
        self._ensure_files()

    def _resolve_path(self, configured: str | None, default: Path) -> Path:
        if not configured:
            path = default
        else:
            path = Path(configured)
            if not path.is_absolute():
                path = REPO_ROOT / path
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _ensure_files(self) -> None:
        if not self.policy_path.exists():
            self.policy_path.write_text(json.dumps(DEFAULT_POLICY_STATE, indent=2))
        if not self.ledger_path.exists():
            self.ledger_path.write_text(json.dumps([], indent=2))

    def load_policy_state(self) -> dict[str, Any]:
        self._ensure_files()
        state = json.loads(self.policy_path.read_text())
        for key, default in DEFAULT_POLICY_STATE.items():
            state.setdefault(key, list(default) if isinstance(default, list) else default)
        return state

    def save_policy_state(self, state: dict[str, Any]) -> None:
        self.policy_path.write_text(json.dumps(state, indent=2, sort_keys=True))

    def load_actions(self) -> list[dict[str, Any]]:
        self._ensure_files()
        return json.loads(self.ledger_path.read_text())

    def save_actions(self, actions: list[dict[str, Any]]) -> None:
        self.ledger_path.write_text(json.dumps(actions, indent=2, sort_keys=True))

    def normalize_action_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            'action_type': payload['action_type'],
            'target_type': payload['target_type'],
            'target_id': payload['target_id'],
            'actor': payload['actor'],
            'reason': payload['reason'],
            'related_asset_id': payload.get('related_asset_id'),
            'metadata': payload.get('metadata', {}),
        }

    def attestation_hash(self, payload: dict[str, Any]) -> str:
        normalized = json.dumps(self.normalize_action_payload(payload), sort_keys=True, separators=(',', ':'))
        return hashlib.sha256(normalized.encode('utf-8')).hexdigest()

    def create_action_record(self, payload: dict[str, Any], policy_effects: list[str]) -> dict[str, Any]:
        actions = self.load_actions()
        attestation = self.attestation_hash(payload)
        record = {
            **self.normalize_action_payload(payload),
            'action_id': f'gov-{len(actions) + 1:04d}',
            'created_at': utc_now(),
            'status': 'applied',
            'attestation_hash': attestation,
            'policy_effects': policy_effects,
        }
        actions.append(record)
        self.save_actions(actions)
        return record
