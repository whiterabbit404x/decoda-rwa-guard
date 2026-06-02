#!/usr/bin/env python3
"""
Validates that the NIW Strategic Infrastructure Guard positioning documents
exist and contain all required key phrases. Fails closed: any missing file
or missing phrase is a hard failure.
"""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

REQUIRED_DOCS = {
    "docs/NIW_STRATEGIC_INFRASTRUCTURE_GUARD.md": [
        "Strategic Infrastructure Guard",
        "tokenized Treasuries",
        "U.S. Treasury",
        "financial stability",
        "Data Privacy, Data Security, and Cybersecurity Technologies",
        "distributed ledger technologies",
        "digital assets",
        "live evidence",
        "controlled pilot",
        "not broad paid SaaS ready",
    ],
    "artifacts/niw-strategic-infrastructure-guard/evidence-map.json": [
        "Strategic Infrastructure Guard",
        "tokenized Treasuries",
        "U.S. Treasury",
        "financial stability",
        "Data Privacy, Data Security, and Cybersecurity Technologies",
        "distributed_ledger_technologies",
        "digital_assets",
        "live evidence",
        "controlled pilot",
        "not yet proven in production mode",
    ],
}

# Phrase used in the doc to signal "not broad paid SaaS ready"; we check the
# narrative doc for the explicit phrase and the evidence map for its equivalent.
_DOC_PROHIBITED_CLAIM_PHRASE = "not broad paid SaaS ready"
_MAP_PROHIBITED_CLAIM_PHRASE = "not yet proven in production mode"


def check_file(rel_path: str, required_phrases: list[str]) -> list[str]:
    path = REPO_ROOT / rel_path
    if not path.exists():
        return [f"MISSING FILE: {rel_path}"]

    text = path.read_text(encoding="utf-8").lower()
    errors = []
    for phrase in required_phrases:
        if phrase.lower() not in text:
            errors.append(f"MISSING PHRASE in {rel_path}: '{phrase}'")
    return errors


def check_evidence_map_structure() -> list[str]:
    path = REPO_ROOT / "artifacts/niw-strategic-infrastructure-guard/evidence-map.json"
    if not path.exists():
        return []  # already caught by check_file

    errors = []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"INVALID JSON in evidence-map.json: {exc}"]

    required_keys = [
        "product_area",
        "protected_infrastructure",
        "cet_alignment",
        "repo_evidence_files",
        "current_claims_allowed",
        "prohibited_claims",
    ]
    for key in required_keys:
        if key not in data:
            errors.append(f"MISSING KEY in evidence-map.json: '{key}'")

    cet = data.get("cet_alignment", {})
    if "primary_category" not in cet:
        errors.append("MISSING KEY in evidence-map.json: cet_alignment.primary_category")
    subfields = cet.get("subfields", {})
    required_subfields = [
        "distributed_ledger_technologies",
        "digital_assets",
        "digital_payment_technologies",
        "communications_and_network_security",
        "privacy_enhancing_technologies",
    ]
    for sf in required_subfields:
        if sf not in subfields:
            errors.append(f"MISSING CET subfield in evidence-map.json: '{sf}'")

    return errors


def main() -> int:
    all_errors: list[str] = []

    for rel_path, phrases in REQUIRED_DOCS.items():
        all_errors.extend(check_file(rel_path, phrases))

    all_errors.extend(check_evidence_map_structure())

    if all_errors:
        print("NIW POSITIONING VALIDATION FAILED")
        for err in all_errors:
            print(f"  [FAIL] {err}")
        return 1

    print("NIW POSITIONING VALIDATION PASSED")
    for rel_path in REQUIRED_DOCS:
        print(f"  [OK] {rel_path}")
    print("  [OK] evidence-map.json structure valid")
    print("  [OK] all required key phrases present")
    return 0


if __name__ == "__main__":
    sys.exit(main())
