# Security Operating Procedures

## Owner register and evidence rules

These are named accountable roles. The Executive Owner must maintain the personnel-to-role assignment in the restricted compliance system and designate a deputy for each role. A procedure is not operationally staffed until that register contains a primary and backup.

| Named role | Accountability |
|---|---|
| Incident Commander | Incident command, severity, containment authority, timeline, and closure |
| Security Operations Owner | Detection, investigation, evidence preservation, and post-incident monitoring |
| Privacy & Legal Owner | Breach analysis, privilege, regulator/customer notification decisions |
| Communications Owner | Approved internal, customer, public, and status-page communications |
| Site Reliability Owner | Service recovery, failover, recovery objectives, and availability evidence |
| Identity & Access Owner | Joiner/mover/leaver controls, MFA, and access certification |
| Product Security Owner | Vulnerability intake, severity, remediation tracking, and verification |
| Key Custodian | Key generation, activation, rotation, retirement, and recovery |
| Compliance Evidence Owner | Evidence quality, retention, restricted storage, and auditor delivery |

Every execution record must identify the procedure version, environment/scope, start and completion timestamps in UTC, operator, approver/reviewer, source links, exceptions, resulting actions, and evidence hash or immutable system identifier. Secrets and unnecessary personal/customer data must not be copied into tickets.

## Incident response

**Owner:** Incident Commander. **Deputy:** Security Operations Owner.
**Triggers:** suspected unauthorized access, malware, exposed secret, integrity loss, material vulnerability exploitation, customer-data disclosure, or availability event requiring coordinated response.

1. Open a restricted incident record, assign severity, name the commander and scribe, preserve source timestamps, and start an append-only timeline.
2. Validate the signal without destroying evidence. Preserve relevant logs, image/container digests, cloud audit events, database audit anchors, affected commits, and volatile evidence where feasible.
3. Contain using the least-destructive effective action: revoke sessions/credentials, isolate workloads, block indicators, pause affected workers or exports, and preserve customer/workspace boundaries.
4. Notify the Privacy & Legal Owner immediately if regulated, personal, customer confidential, or cross-border data may be involved. Notify the Communications Owner for material customer impact.
5. Determine scope and root cause, eradicate persistence, patch the weakness, rotate affected keys, and document every material decision and command.
6. Recover from known-good artifacts; run authentication, authorization, monitoring, evidence-integrity, queue, and customer-workflow canaries before reopening traffic.
7. Communicate on a severity-appropriate cadence. State confirmed facts, customer impact, mitigations, and next update time; do not speculate.
8. Close only after monitoring shows no recurrence, notification decisions are recorded, evidence is indexed, and corrective actions have owners and due dates.
9. Complete a blameless post-incident review within 5 business days for Severity 1/2 and 10 business days for Severity 3. Track actions to closure.

**Required evidence:** incident ticket, severity and rationale, UTC timeline, responder roster, preserved evidence manifest and hashes, affected assets/data/workspaces, containment/recovery validation, key/session actions, communications, notification decision, root cause, postmortem, and corrective-action tracker.

## Breach notification

**Owner:** Privacy & Legal Owner. **Deputies:** Incident Commander and Communications Owner.
**Deadline rule:** Legal deadlines and contractual terms vary. Counsel records the applicable jurisdictions/contracts and uses the shortest applicable deadline. The operational target is to escalate a suspected personal-data breach to Privacy & Legal within **4 hours** and complete an initial notification assessment within **24 hours**; these internal targets do not replace law or contract.

1. Start the breach-assessment worksheet when confidentiality, integrity, or availability of protected data may be compromised.
2. Identify data controller/processor roles, affected customers and individuals, data categories, volume, geography/residency, safeguards, likely consequences, containment, and ongoing risk.
3. Review customer contracts, DPAs, subprocessor terms, cyber-insurance requirements, and applicable law with counsel. Record deadlines in UTC and the legal basis for notifying or not notifying.
4. Preserve privilege where directed. Ensure notices are accurate, approved by Privacy & Legal and Communications, and include required facts, contact route, mitigations, and updates.
5. Deliver notices through contractually/legal approved channels, retain delivery proof, track regulator/customer questions, and issue corrections when facts materially change.

**Required evidence:** assessment worksheet, data map, jurisdiction/contract matrix, counsel decision, approval history, notice versions, recipient list, delivery timestamps, regulator reference numbers, and follow-up log.

## Disaster recovery

**Owner:** Site Reliability Owner. **Deputies:** Database Reliability Owner and Incident Commander.
**Cadence:** quarterly isolated restore; annual regional/provider exercise; additionally after material recovery architecture changes.

1. Declare the recovery event/exercise, freeze conflicting changes, define the recovery point, and identify safety constraints.
2. Follow the component procedures in `docs/DISASTER_RECOVERY_AND_DATA_GOVERNANCE.md`; never restore over the damaged primary.
3. Verify backup identity and integrity, restore in isolation, run migration and audit/evidence-chain validation, and test historical managed-key retrieval.
4. Fence the old writer before promotion. Restore workers gradually and use idempotency/checkpoint checks to prevent duplicate or synthetic live evidence.
5. Run customer-workflow canaries before normal traffic and measure actual RPO and RTO from provider/source timestamps.
6. Record objective breaches and corrective actions; obtain Site Reliability and Incident Commander approval before ending recovery mode.

**Required evidence:** exercise/incident plan, provider backup and region IDs, configuration snapshots, command/output log, validation JSON, canary results, traffic/DNS timestamps, measured RPO/RTO, integrity results, communications, approvals, and remediation tickets.

## Access review

**Owner:** Identity & Access Owner. **Reviewer:** each system's business owner. **Approver:** Executive Owner for privileged access.
**Cadence:** quarterly; monthly for break-glass accounts; event-driven on termination, role change, or suspected compromise.

1. Export identities, groups, roles, service accounts, API keys, and last-use data directly from the IdP, source control, cloud, database, CI/CD, support, monitoring, and compliance evidence systems.
2. Reconcile to active personnel/contractors and approved service owners. Identify dormant, shared, orphaned, excessive, conflicting, and non-MFA access.
3. Require owners to attest business need and least privilege. Reviewers may not self-approve privileged access.
4. Remove unjustified access within 2 business days (immediately for terminated/high-risk identities); time-bound exceptions require rationale, compensating controls, approver, and expiry.
5. Re-export changed systems to prove removal and have the Compliance Evidence Owner verify completeness.

**Required evidence:** dated source exports, population reconciliation, reviewer decisions, removal/change tickets and timestamps, MFA coverage, exception register, post-change exports, and signed completion summary.

## Key rotation

**Owner:** Key Custodian. **Approver:** Platform Security Owner; destructive retirement also requires Security Operations Owner.
**Minimum schedule:** authentication/signing secrets every 90 days; encryption/evidence-signing KMS keys annually where provider rotation is supported; API/vendor credentials annually. Rotate immediately after suspected exposure, unauthorized access, personnel risk, or cryptographic weakness. Contract/provider requirements override with a shorter interval.

1. Inventory purpose, owner, consumers, provider key/version ID, algorithm/size, activation date, next rotation, and recovery dependency. Never put key material in evidence.
2. Create the new key/version in managed KMS/secret storage with least-privilege policy and audit logging.
3. Deploy dual-read/new-write or verification-only compatibility where historical signatures/ciphertext require it. Canary every consumer and rollback path.
4. Activate the new version, restart/refresh consumers, verify new artifacts name the new version, and revoke sessions where authentication rotation requires it.
5. Remove old encryption/signing authority; retain verification/decryption-only access only for the approved retention period. Retire/destroy after dependency and restore tests plus dual approval.
6. For compromise, follow incident response, rotate all derived/downstream credentials, search for use, and document the exposure window.

**Required evidence:** approved change/incident ticket, inventory entry, old/new non-secret version IDs, KMS audit events, deployment/canary output, consumer coverage, activation/retirement timestamps, historical verification test, rollback outcome, and dual approvals.

## Vulnerability remediation

**Owner:** Product Security Owner. **Remediation owner:** owning Engineering or Platform Owner.
**Clock start:** earliest credible discovery time. **Clock stops:** verified remediation in production, or an approved time-bound exception with compensating controls. Actively exploited issues are handled as incidents regardless of score.

| Severity | Examples | Triage target | Remediation SLA |
|---|---|---:|---:|
| Critical | Known exploitation, exposed secret, unauthenticated RCE, material auth/tenant bypass | 4 hours | 24 hours |
| High | High-impact exploit with feasible path; sensitive-data exposure | 1 business day | 7 calendar days |
| Medium | Meaningful weakness requiring conditions or limited impact | 3 business days | 30 calendar days |
| Low | Defense-in-depth or low-likelihood/impact issue | 10 business days | 90 calendar days |

1. Normalize duplicate scanner/researcher findings into one restricted ticket with source, affected versions/assets, exploitability, severity rationale, and SLA dates.
2. Validate safely; do not test against customer data without written authorization. Raise severity for internet exposure, tenant crossing, sensitive data, or known exploitation.
3. Assign an owner and mitigation. Critical/high findings block release unless fixed or accepted by the Product Security Owner and Executive Owner.
4. A risk acceptance must state business rationale, compensating controls, residual risk, owner, and expiry no longer than 30 days (critical/high) or 90 days (medium/low). It is not silent suppression.
5. Retest the fix, deploy it, verify production versions/configuration, update SBOMs, and close with evidence. Track SLA exceptions as control failures.

**Required evidence:** original report/scanner output, ticket, severity and CVE/CWE/CVSS where applicable, affected inventory/SBOM, timestamps, mitigation/fix commits and releases, test and production verification, communications, and approved exception/expiry if used.

## Vendor management

**Owner:** Vendor Risk Owner. **Privacy approver:** Privacy & Legal Owner.
Before a vendor receives production access or customer data, classify inherent risk and review security assurance, privacy terms/DPA, subprocessors, breach notice, data location, encryption, access controls, deletion/return, availability/BCP, financial/operational dependency, and exit plan. Critical/high vendors are reassessed annually and on material incidents/changes; others at least biennially. Findings require a documented accept/mitigate/avoid/transfer decision and owner.

**Required evidence:** intake, data-flow and risk tier, due-diligence artifacts, DPA/contract/security addendum, findings, approval, review date, monitoring/incident records, and termination/deletion confirmation.
