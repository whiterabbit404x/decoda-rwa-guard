# Security release gates and vulnerability exceptions

The release workflow fails closed on mandatory SAST, dependency, secret, container, and configuration scans. High and critical findings with an available fix are release blockers; secret and SAST findings are always blockers. Scanner reports, API/web SBOMs, image digests, keyless signature bundles, and SLSA provenance attestations are retained in the security proof artifact.

## Time-limited exceptions

Exceptions are permitted only for identified dependency or container vulnerabilities that cannot be remediated before release. Add an entry to `security/vulnerability-exceptions.json` with all fields below:

```json
{
  "id": "RISK-1234",
  "scanner": "pip-audit",
  "vulnerability_id": "CVE-2026-12345",
  "scope": "services/api/requirements.txt",
  "justification": "Why exploitation is not currently reachable and the compensating control.",
  "owner": "security-team@example.com",
  "approved_by": "security-approver@example.com",
  "created_at": "2026-06-08T00:00:00Z",
  "expires_at": "2026-06-22T00:00:00Z"
}
```

Allowed scanners are `pip-audit`, `npm-audit`, `trivy-api`, and `trivy-web`. Every exception requires a unique tracking ID, named owner and approver, exact vulnerability ID and scope, justification, and UTC timestamps. Exceptions may last at most 30 days, may not be expired, and must be approved in code review. The workflow validates this file before scanning and generates scanner-specific ignore configuration. SAST, secret, and infrastructure/configuration findings cannot be excepted.
