"""Evidence packages and prospect reports for external findings.

Sealing reuses Decoda's evidence machinery unchanged:

  * ``evidence_signing.build_evidence_manifest`` — canonical-JSON SHA-256 per
    file, manifest hash, Merkle-sealed (schema 2.0)
  * ``evidence_signing.seal_manifest`` — HMAC-SHA256 seal, plus an Ed25519
    public-key signature when a key is provisioned
  * ``evidence_signing.verify_bundle`` / ``evidence_ed25519`` — verification

Two document shapes are produced, for two different readers:

  evidence package   internal. Everything needed to re-verify the finding:
                     protocol, address, chain, transaction, block, time, the
                     decoded event, the raw telemetry used, the finding, the AI
                     analysis, the evidence SHA-256 and the disclaimer.
  prospect report    shareable. The same observed facts and analysis, the
                     evidence package's verification status and hash — and
                     NOTHING internal: no Decoda ids, no scoring or confidence,
                     no triage status, no RPC provider, no user, no customer.

Both carry ``monitoring_scope = external_public`` and the independent-
monitoring disclaimer.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from services.api.app import evidence_signing
from services.api.app.domains.external_watchlist import config as ewc

logger = logging.getLogger(__name__)

EVIDENCE_SCHEMA = 'decoda-external-watchlist-evidence-v1'
PROSPECT_REPORT_SCHEMA = 'decoda-external-prospect-report-v1'
EVIDENCE_FILE = 'evidence.json'
REPORT_FILE = 'prospect-report.json'


class EvidenceSealingUnavailable(RuntimeError):
    code = 'EVIDENCE_SIGNING_UNAVAILABLE'


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, 'isoformat') else str(value)


def _sha256(document: Any) -> str:
    return hashlib.sha256(evidence_signing.canonical_json(document)).hexdigest()


def _seal(*, package_id: str, export_type: str, files: dict[str, Any], generated_at: str,
          generated_by: str | None, source_type: str, source_id: str, required: list[str]) -> dict[str, Any]:
    try:
        manifest, _bytes = evidence_signing.build_evidence_manifest(
            export_id=package_id,
            export_type=export_type,
            # No workspace exists for an external protocol; the manifest says so
            # rather than borrowing one.
            workspace_id=None,  # type: ignore[arg-type]
            generated_at=generated_at,
            generated_by_user_id=generated_by,
            source_resource_type=source_type,
            source_resource_id=source_id,
            storage_backend='database',
            file_values=files,
            seal_merkle=True,
            required_artifacts=required,
            file_provenance={path: {'domain': 'EXTERNAL_PUBLIC', 'source_record_type': source_type} for path in files},
        )
        seal = evidence_signing.seal_manifest(manifest)
    except RuntimeError as exc:
        logger.warning('external_watchlist_evidence_seal_unavailable reason=%s', type(exc).__name__)
        raise EvidenceSealingUnavailable(str(exc)) from exc
    return {'manifest': manifest, 'seal': seal}


def build_evidence_package(
    *,
    watchlist: dict[str, Any],
    target: dict[str, Any] | None,
    finding: dict[str, Any],
    events: list[dict[str, Any]],
    generated_by: str | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    moment = now or datetime.now(timezone.utc)
    package_id = str(uuid.uuid4())
    network = str(finding.get('network') or (target or {}).get('network') or '')
    meta = ewc.SUPPORTED_NETWORKS.get(network, {})
    primary = next((e for e in events if str(e.get('id')) == str(finding.get('primary_event_id'))), events[0] if events else None)
    document = {
        'schema': EVIDENCE_SCHEMA,
        'monitoring_scope': ewc.MONITORING_SCOPE_EXTERNAL_PUBLIC,
        'execution_authority': ewc.EXECUTION_AUTHORITY_NONE,
        'protocol': {'name': watchlist.get('name'), 'website_url': watchlist.get('website_url')},
        'monitored_address': (target or {}).get('address') or finding.get('contract_address'),
        'monitored_target_type': (target or {}).get('target_type'),
        'monitored_target_label': (target or {}).get('label'),
        'chain': {'network': network, 'chain_id': finding.get('chain_id'), 'label': meta.get('label')},
        'transaction_hash': finding.get('tx_hash'),
        'block_number': finding.get('block_number'),
        'timestamp': _iso(finding.get('observed_at')),
        'timestamp_source': (primary or {}).get('observed_at_source'),
        'decoded_event': {
            'event_name': (primary or {}).get('event_name'),
            'contract_address': finding.get('contract_address'),
            'log_index': finding.get('log_index'),
            'topic0': (primary or {}).get('topic0'),
            'parameters': finding.get('decoded') or {},
            'decode_status': (primary or {}).get('decode_status'),
        },
        'telemetry_used': [
            {
                'event_name': event.get('event_name'),
                'contract_address': event.get('contract_address'),
                'transaction_hash': event.get('tx_hash'),
                'block_number': event.get('block_number'),
                'log_index': event.get('log_index'),
                'observed_at': _iso(event.get('observed_at')),
                'observed_at_source': event.get('observed_at_source'),
                'ingest_source': event.get('ingest_source'),
                'payload_sha256': event.get('payload_sha256'),
                'raw_log': event.get('raw_log'),
            }
            for event in events
        ],
        'finding': {
            'title': finding.get('title'),
            'finding_type': finding.get('finding_type'),
            'finding_class': finding.get('finding_class'),
            'finding_class_label': ewc.FINDING_CLASS_LABELS.get(str(finding.get('finding_class'))),
            'severity': finding.get('severity'),
            'explanation': finding.get('explanation'),
            'previous_state': finding.get('previous_state'),
            'new_state': finding.get('new_state'),
            'initiator': finding.get('initiator'),
            'rule_key': finding.get('rule_key'),
            'detector_version': finding.get('detector_version'),
            'detected_at': _iso(finding.get('detected_at')),
        },
        'ai_analysis': {
            key: (finding.get('ai_analysis') or {}).get(key)
            for key in ('observed_fact', 'decoda_interpretation', 'operational_authorization', 'source')
        },
        'provenance': {
            'collection': 'independent_public_monitoring',
            'data_source': 'public_evm_json_rpc',
            'organization_authorized_monitoring': 'not_established',
        },
        'disclaimer': ewc.EVIDENCE_DISCLAIMER,
        'disclaimer_version': ewc.DISCLAIMER_VERSION,
        'generated_at': moment.isoformat(),
    }
    evidence_sha256 = _sha256(document)
    files = {EVIDENCE_FILE: document}
    sealed = _seal(
        package_id=package_id, export_type='external_watchlist_evidence', files=files,
        generated_at=moment.isoformat(), generated_by=generated_by,
        source_type='external_watchlist_finding', source_id=str(finding.get('id')), required=[EVIDENCE_FILE],
    )
    return {
        'id': package_id,
        'package_type': 'evidence_package',
        'files': files,
        'manifest': sealed['manifest'],
        'seal': sealed['seal'],
        'manifest_sha256': sealed['manifest']['manifest_sha256'],
        'evidence_sha256': evidence_sha256,
        'signature_algorithm': sealed['seal'].get('signature_algorithm'),
        'generated_at': moment,
    }


def verify_package(row: dict[str, Any]) -> dict[str, Any]:
    """Re-verify a stored package: file hashes, manifest hash, HMAC seal, and the
    Ed25519 signature when one is present. Fails closed on anything unreadable."""
    files = row.get('files') if isinstance(row.get('files'), dict) else {}
    manifest = row.get('manifest') if isinstance(row.get('manifest'), dict) else {}
    seal = row.get('seal') if isinstance(row.get('seal'), dict) else {}
    dev_seal = 'warning' in seal
    try:
        result = evidence_signing.verify_bundle(
            files, manifest, seal,
            # A development seal is checked against the development secret it was
            # made with, and is REPORTED as non-production below.
            signing_secret=evidence_signing._DEV_FALLBACK_SECRET if dev_seal else None,
        )
    except Exception as exc:  # noqa: BLE001 - verification must fail closed
        return {'status': 'unverified', 'valid': False, 'errors': [type(exc).__name__]}
    errors = list(result.get('errors') or [])
    document = files.get(EVIDENCE_FILE) or files.get(REPORT_FILE)
    if document is not None and _sha256(document) != str(row.get('evidence_sha256') or ''):
        errors.append('evidence_sha256_mismatch')
    ed25519_state = 'absent'
    signatures = seal.get('signatures') if isinstance(seal.get('signatures'), list) else []
    if signatures:
        try:
            from services.api.app import evidence_ed25519

            document_sig = signatures[0]
            public_key = evidence_ed25519.public_key_for(str(document_sig.get('key_id') or ''))
            ed25519_state = 'valid' if public_key and evidence_ed25519.verify_signature_document(
                document_sig, str(manifest.get('manifest_sha256') or ''), public_key,
            ) else 'invalid'
        except Exception:  # noqa: BLE001
            ed25519_state = 'invalid'
        if ed25519_state == 'invalid':
            errors.append('ed25519_signature_invalid')
    valid = not errors
    return {
        'status': 'verified' if valid else 'verification_failed',
        'valid': valid,
        'errors': errors,
        'manifest_sha256': manifest.get('manifest_sha256'),
        'evidence_sha256': row.get('evidence_sha256'),
        'signature_algorithm': seal.get('signature_algorithm'),
        'public_key_signature': ed25519_state,
        'production_secret': not dev_seal,
        'warning': seal.get('warning'),
    }


# ── prospect report ──────────────────────────────────────────────────────────
_HEADINGS = {
    'administrative_change': 'Administrative Change Detected',
    'privileged_configuration_change': 'Administrative Change Detected',
    'observed_anomaly': 'Observed Anomaly Detected',
    'unusual_activity': 'Unusual Activity Observed',
    'review': 'On-Chain Change Observed',
}


def observed_change_sentence(finding: dict[str, Any]) -> str:
    """One plain sentence describing what changed, from decoded values only."""
    explanation = str(finding.get('explanation') or '').strip()
    return explanation or str(finding.get('title') or 'An on-chain change was observed.')


def build_prospect_report(
    *,
    watchlist: dict[str, Any],
    finding: dict[str, Any],
    verification: dict[str, Any],
    generated_by: str | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    moment = now or datetime.now(timezone.utc)
    network = str(finding.get('network') or '')
    meta = ewc.SUPPORTED_NETWORKS.get(network, {})
    analysis = finding.get('ai_analysis') if isinstance(finding.get('ai_analysis'), dict) else {}
    tx_hash = finding.get('tx_hash')
    contract = finding.get('contract_address')
    report = {
        'schema': PROSPECT_REPORT_SCHEMA,
        'heading': _HEADINGS.get(str(finding.get('finding_class')), 'On-Chain Change Observed'),
        'monitoring_scope': ewc.MONITORING_SCOPE_EXTERNAL_PUBLIC,
        'source_label': 'External Public Monitoring',
        'protocol': watchlist.get('name'),
        'protocol_website': watchlist.get('website_url'),
        'network': meta.get('label') or network,
        'contract': contract,
        'contract_explorer_url': ewc.explorer_address_url(network, contract) if contract else None,
        'transaction': tx_hash,
        'transaction_explorer_url': ewc.explorer_tx_url(network, tx_hash) if tx_hash else None,
        'block_number': finding.get('block_number'),
        'observed_time': _iso(finding.get('observed_at')),
        'observed_change': observed_change_sentence(finding),
        'change_title': finding.get('title'),
        'decoda_analysis': {
            'observed_fact': analysis.get('observed_fact'),
            'decoda_interpretation': analysis.get('decoda_interpretation'),
            'operational_authorization': analysis.get('operational_authorization') or ewc.AUTHORIZATION_UNKNOWN_STATEMENT,
        },
        'evidence_verification': {
            'status': verification.get('status'),
            'evidence_sha256': verification.get('evidence_sha256'),
            'manifest_sha256': verification.get('manifest_sha256'),
            'signature_algorithm': verification.get('signature_algorithm'),
            'public_key_signature': verification.get('public_key_signature'),
            'production_secret': verification.get('production_secret'),
        },
        'disclaimer': ewc.EVIDENCE_DISCLAIMER,
        'disclaimer_version': ewc.DISCLAIMER_VERSION,
        'prepared_by': 'Decoda Security',
        'generated_at': moment.isoformat(),
    }
    evidence_sha256 = _sha256(report)
    files = {REPORT_FILE: report}
    package_id = str(uuid.uuid4())
    sealed = _seal(
        package_id=package_id, export_type='external_watchlist_prospect_report', files=files,
        generated_at=moment.isoformat(), generated_by=generated_by,
        source_type='external_watchlist_finding', source_id=str(finding.get('id')), required=[REPORT_FILE],
    )
    return {
        'id': package_id,
        'package_type': 'prospect_report',
        'report': report,
        'files': files,
        'manifest': sealed['manifest'],
        'seal': sealed['seal'],
        'manifest_sha256': sealed['manifest']['manifest_sha256'],
        'evidence_sha256': evidence_sha256,
        'signature_algorithm': sealed['seal'].get('signature_algorithm'),
        'generated_at': moment,
    }
