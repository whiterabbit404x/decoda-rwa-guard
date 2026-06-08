# Secure Release Requirements and SOC 2 Evidence

No production release may be promoted until the release owner links a complete evidence bundle to the immutable commit and artifact digests. A waiver is time-limited, risk-assessed, approved by the security owner and service owner, and retained with the same evidence; a waiver does not bypass exploitable critical findings, unsigned artifacts, or missing provenance.

## Required release gates

1. **Independent penetration testing:** an organizationally independent qualified tester performs at least annual and material-change testing across tenant isolation, authentication/session management, authorization, API/webhook/SCIM surfaces, cloud configuration, and abuse cases. Critical/high findings must be retested. The current report, scope, tester independence, remediation plan, and retest letter are release evidence. A release after a material architecture/authentication change is blocked until security documents whether targeted retesting is required.
2. **Dependency and source scanning:** scan Python and JavaScript lockfiles plus first-party source on every pull request and release. The scanner database and tool versions are recorded. Critical/high findings fail the release unless the remediation SLA and exception process below are satisfied.
3. **Container scanning:** scan each final image by immutable digest after build, including OS packages, language dependencies, secrets, and dangerous configuration. Scanning only the source tree or base image is insufficient.
4. **SBOM:** generate CycloneDX or SPDX SBOMs for every application and image, bind them to commit and image/artifact digests, upload them to immutable release evidence, and make them available for incident response and customer assurance.
5. **Signed artifacts and provenance:** keyless-sign images and release artifacts with an approved workload identity; produce SLSA-compatible provenance; verify signature, identity/issuer, digest, and provenance before deployment. Mutable tags are never deployment authority.
6. **Test and control evidence:** retain unit/integration/e2e results, migration checks, tenant-isolation tests, CSP assertions, security scans, SBOMs, signatures/provenance, approvals, deployment attestation, change ticket, rollback plan, and post-deploy validation.

## Vulnerability remediation SLAs

| Severity | Remediation deadline | Release treatment |
|---|---:|---|
| Critical / known exploited / exposed secret | 24 hours (immediate containment) | Blocks release and may trigger incident response. No routine waiver. |
| High | 7 calendar days | Blocks release when exploitable in the shipped context; exception requires compensating controls and security approval. |
| Medium | 30 calendar days | Tracked owner/date required; overdue findings block release. |
| Low | 90 calendar days | Tracked in normal backlog; review at deadline. |

Severity considers exploitability, reachability, data sensitivity, privilege, tenant boundary, and compensating controls—not scanner score alone. False positives require reproducible evidence and reviewer approval. Exceptions include finding/CVE, affected digest, business justification, exposure analysis, compensating controls, owner, expiry (maximum 30 days), and approvers. Reopen exceptions when the artifact, exploit intelligence, or exposure changes.

## SOC 2 evidence collection

The release workflow and release owner collect evidence mapped to at least:

* **CC6 Logical access:** reviewer approvals, branch protection result, workload identity, signing identity, least-privilege deployment authorization, credential-rotation/revocation events.
* **CC7 System operations/security monitoring:** SAST/dependency/container/secret scan outputs, vulnerability disposition, monitoring/alert validation, incident links.
* **CC8 Change management:** change request, commit/PR, test results, migration/rollback plan, artifact digests, signed provenance, deployment approval and attestation.
* **A1 Availability:** backup/restore validation status, recovery impact assessment, health/readiness and post-deploy canaries.
* **C1 Confidentiality:** tenant-isolation results, encryption/key-version readiness, data-handling and retention impact assessment.

Evidence must be machine-readable where practical, immutable, access-controlled, timestamped in UTC, and retained according to the audit/evidence retention policy (minimum one audit period plus the current period unless legal hold or contract requires longer). Quarterly, control owners sample releases and reconcile workflow runs, deployed digests, signatures, vulnerability tickets, exceptions, and production attestations. Missing or inconsistent evidence is a control exception with an owner and due date.

## Release owner checklist

- [ ] Independent penetration-test evidence is current for the release scope; material changes have a documented retest decision.
- [ ] Dependency/source and final-container scans passed against current vulnerability intelligence.
- [ ] SBOMs are generated for application packages and every image digest.
- [ ] Artifacts/images and provenance are signed and verified with approved identities.
- [ ] Findings meet remediation SLAs; exceptions are unexpired and approved.
- [ ] SOC 2 evidence bundle contains approvals, tests, scans, SBOMs, signatures, provenance, migration/rollback plan, deployment attestation, and post-deploy checks.
- [ ] Credential/key migration and rollback preserve required historical versions and revocation history.
