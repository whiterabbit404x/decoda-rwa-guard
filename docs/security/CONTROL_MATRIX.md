# Security and Compliance Control Matrix

**Accountable owner:** Security & Compliance Owner
**Control approver:** Executive Owner
**Evidence custodian:** Compliance Evidence Owner
**Review cadence:** Quarterly; control failures are reviewed immediately

Status meanings: **Implemented** means the repository contains the control mechanism or procedure; **Operational evidence required** means execution records must still be collected for each period; **Planned** means the control is not yet operating and must not be represented as complete.

## Access control

| ID | Control activity | Owner | Frequency / trigger | Required evidence | Status |
|---|---|---|---|---|---|
| AC-01 | Enforce unique identities, least privilege, workspace scoping, and role-based authorization. | Identity & Access Owner | Continuous; every authorization change | RBAC test results, role definitions, access-denial logs, approved exceptions | Implemented; operational evidence required |
| AC-02 | Require MFA for privileged production, cloud, source-control, and evidence-system access. | Identity & Access Owner | Continuous | IdP MFA policy export and privileged-user coverage report | Operational evidence required |
| AC-03 | Review privileged and production access; remove stale, excessive, and conflicting access. | Identity & Access Owner | Quarterly and within 24 hours of termination | Signed access-review record, source exports, removals, exception expiry | Procedure implemented; operational evidence required |
| AC-04 | Revoke workforce access and rotate exposed shared credentials after departure or role change. | People Operations Owner | Within 24 hours; immediate for involuntary/high-risk termination | Offboarding ticket, IdP/SCM/cloud revocation timestamps, key-rotation record | Operational evidence required |

## Change management

| ID | Control activity | Owner | Frequency / trigger | Required evidence | Status |
|---|---|---|---|---|---|
| CM-01 | Require tracked change, peer review, passing CI, and protected-branch approval before production merge. | Engineering Owner | Every production change | Pull request, approvals, CI run, commit and deployment identifiers | Implemented; repository settings evidence required |
| CM-02 | Validate database migrations in sequence and against an ephemeral PostgreSQL database before release. | Database Reliability Owner | Every migration/change | Migration validation log and successful apply/reapply CI run | Implemented by security CI |
| CM-03 | Record emergency changes and complete retrospective review. | Incident Commander | Every emergency change; review within 2 business days | Incident/change ticket, approval, test evidence, retrospective | Procedure implemented; operational evidence required |

## Vulnerability management

| ID | Control activity | Owner | Frequency / trigger | Required evidence | Status |
|---|---|---|---|---|---|
| VM-01 | Audit Python and JavaScript dependencies and block release on unaccepted vulnerabilities. | Product Security Owner | Every pull request and weekly scheduled run | `pip-audit`/`npm audit` logs, ticket or time-bound risk acceptance | Implemented by security CI |
| VM-02 | Scan source for secrets and security weaknesses. | Product Security Owner | Every pull request, push to protected branches, and weekly | Gitleaks and Bandit/SAST reports, triage records | Implemented by security CI |
| VM-03 | Scan the production container image and generate an SBOM. | Platform Security Owner | Every pull request/release and weekly | Image digest, Trivy report, CycloneDX/SPDX SBOM, provenance link | Implemented by security CI |
| VM-04 | Triage and remediate findings within severity SLAs. | Product Security Owner | Continuous | Finding ticket, severity rationale, first-seen/fixed timestamps, retest | Procedure implemented; operational evidence required |

## Secure development

| ID | Control activity | Owner | Frequency / trigger | Required evidence | Status |
|---|---|---|---|---|---|
| SD-01 | Apply secure design review and threat-model updates to material changes. | Product Security Owner | Material architecture/data-flow/auth change | Design review, threat-model diff, abuse cases, approvals | Procedure implemented; operational evidence required |
| SD-02 | Pin direct production dependencies and install from committed lock files. | Engineering Owner | Every dependency change | Manifest/lock diff, dependency-update PR, CI audit | Implemented |
| SD-03 | Keep demo/fallback data separate from live workspace data and fail closed in production. | Application Security Owner | Continuous; every affected change | Isolation tests, runtime configuration evidence, release proof | Implemented; operational evidence required |

## Logging and monitoring

| ID | Control activity | Owner | Frequency / trigger | Required evidence | Status |
|---|---|---|---|---|---|
| LM-01 | Log authentication, authorization, administrative, security, export, and key lifecycle events without secrets. | Security Operations Owner | Continuous | Logging configuration, sampled events, redaction tests | Implemented in part; operational validation required |
| LM-02 | Protect audit integrity and monitor security/availability alerts. | Security Operations Owner | Continuous; alert review daily | Hash-chain verification, alert history, on-call acknowledgement | Implemented; operational evidence required |
| LM-03 | Synchronize production time and retain logs according to approved policy. | Platform Security Owner | Continuous; quarterly validation | Provider time configuration and retention setting export | Operational evidence required |

## Incident response

| ID | Control activity | Owner | Frequency / trigger | Required evidence | Status |
|---|---|---|---|---|---|
| IR-01 | Classify, contain, eradicate, recover, communicate, and preserve incident evidence. | Incident Commander | Every suspected incident; tabletop semiannually | Incident record, timeline, decisions, evidence manifest, communications, postmortem | Procedure implemented; exercise evidence required |
| IR-02 | Assess notification obligations and issue approved notices within applicable deadlines. | Privacy & Legal Owner | Every incident involving personal/confidential data | Jurisdiction assessment, affected-party analysis, counsel decision, notice and delivery proof | Procedure implemented; operational evidence required |

## Availability

| ID | Control activity | Owner | Frequency / trigger | Required evidence | Status |
|---|---|---|---|---|---|
| AV-01 | Monitor service health and recovery-objective consumption; communicate qualifying incidents. | Site Reliability Owner | Continuous | SLO dashboards, alert records, incident updates | Implemented in part; production evidence required |
| AV-02 | Test regional/provider recovery and record measured RTO/RPO. | Site Reliability Owner | Annually and after material recovery changes | Exercise plan, timestamps, validation output, corrective actions | Procedure implemented; exercise evidence required |

## Backup recovery

| ID | Control activity | Owner | Frequency / trigger | Required evidence | Status |
|---|---|---|---|---|---|
| BR-01 | Maintain encrypted, access-controlled PostgreSQL PITR and object-storage versioning/replication. | Database Reliability Owner | Continuous; configuration reviewed quarterly | Provider configuration exports, backup success logs, access policy | Operational evidence required |
| BR-02 | Restore backups into isolation and verify migrations, integrity chains, keys, and workflow canaries. | Database Reliability Owner | Quarterly | Restore ID, validation run, measured RPO/RTO, reviewer approval | Tooling/procedure implemented; exercise evidence required |

## Vendor management

| ID | Control activity | Owner | Frequency / trigger | Required evidence | Status |
|---|---|---|---|---|---|
| TP-01 | Perform risk-tiered due diligence before a vendor handles production/customer data. | Vendor Risk Owner | Before onboarding; annually for critical/high vendors | Security report/certification, DPA, breach terms, BCP, risk decision | Procedure implemented; operational evidence required |
| TP-02 | Maintain subprocessors, purpose, location, data classes, and exit strategy. | Privacy & Legal Owner | On change; quarterly review | Approved subprocessor register, customer notice, deletion/exit evidence | Register established; deployment facts require confirmation |

## Data retention

| ID | Control activity | Owner | Frequency / trigger | Required evidence | Status |
|---|---|---|---|---|---|
| DR-01 | Maintain approved retention schedules by data class and suspend deletion under legal hold. | Privacy & Legal Owner | Annual review; every hold/change | Approved schedule, legal-hold record, system configuration | Implemented in data model; policy values require approval |
| DR-02 | Execute deletion with authorization, integrity logging, and provider-aware completion status. | Data Governance Owner | Per approved request/schedule | Request, approval, deletion events, object-lock/provider result | Implemented; operational evidence required |

## Cryptographic key management

| ID | Control activity | Owner | Frequency / trigger | Required evidence | Status |
|---|---|---|---|---|---|
| KM-01 | Store production keys in a managed secret/KMS service; prohibit plaintext repository storage. | Platform Security Owner | Continuous | KMS policy/configuration, secret scan, access log sample | Application support implemented; provider evidence required |
| KM-02 | Rotate keys on schedule and immediately on suspected compromise while preserving verification-only history where needed. | Key Custodian | Per key schedule and incident trigger | Change ticket, old/new version IDs, timestamps, canary/retest, retirement approval | Procedure/data model implemented; operational evidence required |
| KM-03 | Enforce dual review for destructive key retirement and test historical evidence verification. | Key Custodian | Every retirement; quarterly verification test | Two approvals, verification result, destruction/retirement receipt | Procedure implemented; operational evidence required |
