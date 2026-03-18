from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from phase1_local.dev_support import REPO_ROOT, utc_now

DEFAULT_INCIDENT_LEDGER_PATH = REPO_ROOT / '.data' / 'reconciliation_incidents.json'


class ReconciliationStore:
    def __init__(self, ledger_path: Path | None = None) -> None:
        self.ledger_path = ledger_path or self._resolve_path(os.getenv('RECONCILIATION_LEDGER_PATH'), DEFAULT_INCIDENT_LEDGER_PATH)
        self._ensure_file()

    def _resolve_path(self, configured: str | None, default: Path) -> Path:
        path = default if not configured else Path(configured)
        if not path.is_absolute():
            path = REPO_ROOT / path
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _ensure_file(self) -> None:
        if not self.ledger_path.exists():
            self.ledger_path.write_text(json.dumps([], indent=2))

    def load_incidents(self) -> list[dict[str, Any]]:
        self._ensure_file()
        return json.loads(self.ledger_path.read_text())

    def save_incidents(self, incidents: list[dict[str, Any]]) -> None:
        self.ledger_path.write_text(json.dumps(incidents, indent=2, sort_keys=True))

    def normalize_incident_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            'event_type': payload['event_type'],
            'trigger_source': payload['trigger_source'],
            'related_asset_id': payload['related_asset_id'],
            'affected_assets': sorted(payload.get('affected_assets', [])),
            'affected_ledgers': sorted(payload.get('affected_ledgers', [])),
            'severity': payload['severity'],
            'status': payload['status'],
            'summary': payload['summary'],
            'metadata': payload.get('metadata', {}),
        }

    def attestation_hash(self, payload: dict[str, Any]) -> str:
        normalized = json.dumps(self.normalize_incident_payload(payload), sort_keys=True, separators=(',', ':'))
        return hashlib.sha256(normalized.encode('utf-8')).hexdigest()

    def create_incident(self, payload: dict[str, Any]) -> dict[str, Any]:
        incidents = self.load_incidents()
        normalized = self.normalize_incident_payload(payload)
        attestation = self.attestation_hash(payload)
        record = {
            **normalized,
            'event_id': f"evt-{len(incidents) + 1:04d}",
            'created_at': utc_now(),
            'attestation_hash': attestation,
            'fingerprint': attestation[:16],
        }
        incidents.append(record)
        self.save_incidents(incidents)
        return record
