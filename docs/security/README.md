# Enterprise Security Package

**Document owner:** Security & Compliance Owner
**Approver:** Executive Owner
**Review cadence:** Quarterly and after material architecture, vendor, or regulatory changes
**Classification:** Customer-shareable, except linked evidence that is marked confidential

This directory is the canonical index for Decoda RWA Guard's security and assurance program. It describes implemented controls, required operating procedures, and evidence expectations. It does **not** claim that an independent penetration test, SOC 2 examination, ISO certification, or other third-party audit has completed unless a dated report is listed in the audit-evidence register below.

## Package contents

| Customer diligence topic | Canonical artifact |
|---|---|
| Control ownership, frequency, evidence, and status | [Control matrix](./CONTROL_MATRIX.md) |
| Incident response and breach notification | [Security operating procedures](./SECURITY_OPERATING_PROCEDURES.md#incident-response) |
| Disaster recovery and business continuity | [Security operating procedures](./SECURITY_OPERATING_PROCEDURES.md#disaster-recovery) and [technical recovery runbook](../DISASTER_RECOVERY_AND_DATA_GOVERNANCE.md) |
| Access reviews, key rotation, vulnerability remediation | [Security operating procedures](./SECURITY_OPERATING_PROCEDURES.md) |
| Architecture, trust boundaries, data flow, and threat model | [Architecture, data flow, and threat model](./ARCHITECTURE_DATA_FLOW_THREAT_MODEL.md) |
| Subprocessors and data residency | [Subprocessors and data residency](./SUBPROCESSORS_AND_DATA_RESIDENCY.md) |
| Availability commitments, RTO, and RPO | [SLA and recovery objectives](./SLA_AND_RECOVERY_OBJECTIVES.md) |
| Retention and deletion | [Control matrix](./CONTROL_MATRIX.md#data-retention) and [technical governance runbook](../DISASTER_RECOVERY_AND_DATA_GOVERNANCE.md) |
| Penetration test and independent assurance plan | [Assurance roadmap](./ASSURANCE_ROADMAP.md) |
| Dependency, SAST, secret, container, migration, and SBOM gates | [Security CI workflow](../../.github/workflows/security-gates.yml) |
| Vulnerability remediation deadlines | [Severity and remediation SLA](./SECURITY_OPERATING_PROCEDURES.md#vulnerability-remediation) |

## Audit-evidence register

Evidence must be stored in the restricted compliance evidence system, not committed when it contains customer data, secrets, vulnerability details, personnel data, or confidential auditor material. Each evidence item must have an immutable identifier, collection period, owner, reviewer, source-system link, and SHA-256 hash where exportable.

| Evidence | Current state as of 2026-06-07 | Disclosure rule |
|---|---|---|
| CI security-gate run and generated SBOM | Implemented by repository workflow; attach successful run URL and artifact digest per release | Share summary or artifact on request |
| Control operating evidence | Collection framework established; operating evidence accumulates according to the matrix cadence | Share sampled/redacted evidence under NDA |
| Independent penetration-test report | **Not yet completed**; commissioning plan and acceptance criteria are in the assurance roadmap | Share executive summary and remediation attestation under NDA after completion |
| SOC 2 Type II report | **Not yet available**; readiness and observation-period plan is in the assurance roadmap | Share final report under NDA after issuance |
| SOC 2 Type I / equivalent readiness assessment | **Not yet available** | Share final independent report under NDA after issuance |
| Disaster-recovery exercise | Procedure and durable run record exist; attach the latest completed exercise evidence when performed | Share redacted exercise summary |

## Evidence request process

The Security & Compliance Owner validates the request, confirms NDA and least-privilege scope, and provides current artifacts through an access-controlled data room. Expired or superseded reports remain versioned but are labeled clearly. Sales and product personnel may not imply certification, a completed assessment, or an unqualified availability commitment that is not present in this package.

## Release security artifacts

Each releasable API image must have a passing dependency audit, secret scan, static analysis, migration validation, high/critical container scan, and a generated CycloneDX **software bill of materials**. The CI run URL, image digest, and SBOM artifact identifier form release evidence; a failed or missing gate requires the vulnerability exception process and authorized release decision.
