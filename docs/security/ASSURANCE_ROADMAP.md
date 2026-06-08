# Independent Assurance and Penetration-Test Roadmap

**Program owner:** Security & Compliance Owner
**Executive sponsor:** Executive Owner
**Evidence owner:** Compliance Evidence Owner

## Truthful current status (2026-06-07)

* No independent penetration-test report is present in this repository.
* No SOC 2 Type II, SOC 2 Type I, ISO 27001, or equivalent independent assurance report is present.
* The control matrix, procedures, evidence requirements, CI gates, architecture/data-flow/threat model, and customer security-package index are established by this change.
* External work is **planned and procurement-dependent**. Documentation of an RFP or plan is not evidence that a firm has been retained or that an audit has passed.

## Penetration-test commission package

**Target:** Execute before asserting enterprise assessment completion and at least annually thereafter, plus after material authentication/tenant-boundary changes.

1. Security & Compliance Owner issues an RFP to at least two qualified independent firms and records independence, tester qualifications, methodology, timing, price, data handling, insurance, and sample deliverables.
2. Required scope includes the public web/API, authentication/session/CSRF, workspace RBAC and cross-tenant isolation, admin/API-key flows, webhook/SSRF surfaces, telemetry/provider ingestion, evidence exports/signatures, business-logic abuse, cloud/container configuration review, and authenticated/unauthenticated testing.
3. Rules of engagement define written authorization, production versus staging boundaries, source-code access, test accounts/workspaces, prohibited destructive tests, emergency contacts, data retention/deletion, vulnerability disclosure, and stop conditions.
4. Deliverables require an executive report, technical findings with reproducible evidence and risk ratings, positive-control observations, attack narrative, affected assets, remediation guidance, and retest letter.
5. Product Security maps findings to the remediation SLA. Critical/high findings block an unqualified completion statement until fixed/retested or explicitly disclosed as residual risk.

**Commissioning evidence required:** approved budget, vendor comparison and conflict check, signed SOW/authorization, scope and rules of engagement, scheduled test dates, report receipt, finding tickets, remediation/retest evidence, and approved customer-facing summary.

## SOC 2 Type II or equivalent path

The default target is SOC 2 Type II covering Security and, after scoping, Availability and Confidentiality. The Security & Compliance Owner may select an equivalent framework (for example ISO 27001 certification) only with an executive-approved customer/control mapping.

| Phase | Exit criteria | Target window after program approval |
|---|---|---:|
| Scope and readiness | In-scope systems/entities, trust-services criteria, owners, policies, vendors, evidence system, and readiness gap assessment approved | 0-60 days |
| Remediation and evidence dry run | High gaps closed; access review, vulnerability, change, backup/restore, incident tabletop, vendor review, and key rotation evidence sampled successfully | 61-120 days |
| Independent point-in-time readiness/Type I (optional) | Independent report or readiness letter received; exceptions have owners/dates | 121-180 days |
| Type II observation | Controls operate for the auditor-agreed period, normally 3-12 months; population evidence retained continuously | After readiness |
| Examination and distribution | Management assertion, auditor testing, exception response, final report, bridge-letter process, restricted customer distribution | After observation |

## Evidence collection calendar

* **Continuous/per change:** pull requests, approvals, CI gates, deployments, incidents, vulnerability tickets, vendor changes, key events.
* **Monthly:** privileged/break-glass review, vulnerability SLA report, backup-success sample, security-alert sample.
* **Quarterly:** full access review, isolated restore, control-owner certification, subprocessor review, KMS/access-log sample.
* **Semiannual:** incident/breach tabletop and evidence-quality audit.
* **Annual:** regional/provider recovery exercise, penetration test, policy/risk assessment, critical-vendor reassessment, key rotation according to schedule.

## Publication gate

The enterprise package may say "controls documented" or "assessment planned" while those statements remain true. It may say "penetration tested," "SOC 2 audited," "SOC 2 compliant," or equivalent only after the Security & Compliance Owner links the current signed independent report, scope, period, exceptions, and approved wording in the audit-evidence register.
