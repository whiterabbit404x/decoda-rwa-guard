# Subprocessors and Data Residency

**Owner:** Privacy & Legal Owner
**Operational maintainer:** Vendor Risk Owner
**Review cadence:** quarterly and before any new subprocessor receives customer data

The repository supports multiple deployment providers and does not prove which optional providers are enabled in a particular production environment. Before customer publication, the owner must replace **Deployment confirmation required** entries with the contracted legal entity, service, processing purpose, data categories, primary/backup region, transfer mechanism, and deletion terms from production contracts and configuration. An empty or illustrative register must not be represented as a complete production subprocessor list.

## Controlled production register

| Service category | Candidate/provider indicated by repository | Purpose | Data categories | Residency/location | Production status |
|---|---|---|---|---|---|
| Application hosting | Railway configuration is present | API/web runtime and deployment logs | Customer requests, operational metadata | Deployment confirmation required | Deployment confirmation required |
| PostgreSQL/Redis hosting | Provider selected at deployment | Authoritative records; ephemeral cache/pub-sub | Customer confidential data; operational metadata | Deployment confirmation required | Deployment confirmation required |
| Object storage | S3-compatible integration / AWS SDK | Evidence exports, manifests, backups where configured | Customer evidence and metadata | Deployment confirmation required | Deployment confirmation required |
| Email delivery | SendGrid, Resend, or SMTP options | Transactional/service email | Email address, message metadata/content | Deployment confirmation required | Optional; configuration confirmation required |
| Billing | Paddle or Stripe options | Subscription and payment administration | Contact, account, billing metadata; card data handled by provider | Deployment confirmation required | Optional; configuration confirmation required |
| Chain/telemetry RPC | Customer/operator-selected EVM provider | Blockchain telemetry collection | Public-chain data, provider request metadata | Deployment confirmation required | Configuration confirmation required |
| Source control/CI | GitHub Actions workflows are present | Source control, CI/CD, security reports | Source code, build metadata, limited test data | Provider terms/region model | Operational confirmation required |
| Monitoring/incident tooling | Provider selected at deployment | Logs, metrics, alerting, incident management | Operational/security metadata; avoid payload secrets | Deployment confirmation required | Deployment confirmation required |

## Residency commitments

No fixed customer-data residency commitment is made by this repository. Contractual residency is set per production deployment and must cover the primary database, replicas/backups, object storage, logs/monitoring, support access, and each subprocessor. Cross-border transfers require Privacy & Legal approval and the applicable transfer mechanism. Support and engineering access location must be disclosed where contractually required.

## Change and customer notice

Before adding or replacing a subprocessor, the Vendor Risk Owner completes due diligence, Privacy & Legal approves the DPA/transfer position, the data-flow/threat model is updated, and customers receive the notice/objection period required by contract. The register retains effective and termination dates. At termination, the owner records access revocation and data return/deletion evidence.
