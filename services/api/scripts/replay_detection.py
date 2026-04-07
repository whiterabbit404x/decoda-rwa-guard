#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from services.api.app import main as api_main
from services.api.app.pilot import ensure_pilot_schema, pg_connection
from services.api.app.threat_payloads import normalize_threat_payload


def _json_rpc_receipt(tx_hash: str) -> dict[str, Any] | None:
    rpc_url = str(os.getenv('EVM_RPC_URL', '')).strip()
    if not rpc_url or not tx_hash:
        return None
    payload = {'jsonrpc': '2.0', 'id': 1, 'method': 'eth_getTransactionReceipt', 'params': [tx_hash]}
    req = Request(rpc_url, data=json.dumps(payload).encode('utf-8'), headers={'Content-Type': 'application/json'})
    with urlopen(req, timeout=20) as resp:
        body = json.loads(resp.read().decode('utf-8') or '{}')
    if body.get('error'):
        raise RuntimeError(f"rpc error: {body['error']}")
    result = body.get('result')
    return result if isinstance(result, dict) else None


def _json_diff(previous: dict[str, Any], replayed: dict[str, Any]) -> dict[str, Any]:
    diff: dict[str, Any] = {}
    keys = set(previous.keys()) | set(replayed.keys())
    for key in sorted(keys):
        if previous.get(key) != replayed.get(key):
            diff[key] = {'previous': previous.get(key), 'replayed': replayed.get(key)}
    return diff


def replay_incident(*, incident_id: str, out_dir: Path) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        rows = connection.execute(
            '''
            SELECT dm.id, dm.alert_id, dm.evidence, a.payload AS alert_payload
            FROM detection_metrics dm
            LEFT JOIN alerts a ON a.id = dm.alert_id
            WHERE dm.incident_id = %s
            ORDER BY dm.detected_at DESC
            ''',
            (incident_id,),
        ).fetchall()
    if not rows:
        raise RuntimeError('No detection_metrics rows found for incident.')

    replay_rows: list[dict[str, Any]] = []
    for row in rows:
        metric = dict(row)
        evidence = metric.get('evidence') if isinstance(metric.get('evidence'), dict) else {}
        payload = metric.get('alert_payload') if isinstance(metric.get('alert_payload'), dict) else {}
        replay_input = dict(payload)
        replay_metadata = replay_input.get('metadata') if isinstance(replay_input.get('metadata'), dict) else {}
        replay_metadata['replay_mode'] = True
        replay_input['metadata'] = replay_metadata
        normalized, normalized_changed = normalize_threat_payload('transaction', replay_input, include_original=False)
        replayed = api_main.proxy_threat('transaction', normalized)
        diff = _json_diff(payload, replayed if isinstance(replayed, dict) else {})
        record = {
            'detection_metric_id': metric.get('id'),
            'alert_id': metric.get('alert_id'),
            'tx_hash': evidence.get('tx_hash'),
            'normalized_changed': normalized_changed,
            'diff': diff,
        }
        tx_hash = str(evidence.get('tx_hash') or '').strip()
        receipt = _json_rpc_receipt(tx_hash) if tx_hash else None
        if receipt:
            receipt_path = out_dir / f"{metric.get('id')}_receipt.json"
            receipt_path.write_text(json.dumps(receipt, indent=2))
            record['receipt_path'] = str(receipt_path)
        replay_rows.append(record)

    summary = {'incident_id': incident_id, 'records': replay_rows}
    (out_dir / 'replay_diff.json').write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description='Replay detection evidence for an incident and print diffs.')
    parser.add_argument('--incident-id', required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    replay_incident(incident_id=args.incident_id, out_dir=Path(args.out).resolve())
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
