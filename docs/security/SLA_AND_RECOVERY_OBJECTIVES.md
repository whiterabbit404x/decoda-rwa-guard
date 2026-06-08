# SLA and Recovery Objectives

**Owner:** Site Reliability Owner
**Contract approver:** Executive Owner and Privacy & Legal Owner

## Service-level terms

No general contractual uptime percentage, service credit, support response, or data-residency term is created by this repository. Customer order forms and MSAs must state the applicable production terms. Marketing may describe architecture targets only as targets, not as measured commitments, until production monitoring and signed terms support the claim.

Recommended enterprise schedule fields are: monthly uptime objective and exclusions; maintenance notice; severity definitions; initial response/update targets; service-credit calculation and claim window; support channels; security-incident notice terms; termination/data-export assistance; and force-majeure/dependency exclusions. Privacy & Legal must approve every deviation.

## Recovery objectives

These are engineering recovery targets, measured from the first confirmed customer impact. The detailed procedures and assumptions are in `docs/DISASTER_RECOVERY_AND_DATA_GOVERNANCE.md`.

| Capability | RPO target | RTO target |
|---|---:|---:|
| PostgreSQL system of record | 5 minutes | 60 minutes |
| Evidence exports and manifests | 15 minutes | 4 hours |
| Redis rate limiting/session revocation cache | 0 minutes for authoritative data | 15 minutes |
| Redis pub/sub alert stream | No durability commitment | 15 minutes |
| Monitoring checkpoints and worker heartbeats | 5 minutes | 30 minutes |
| Webhook and notification queues | 5 minutes | 60 minutes |
| Authentication and managed keys | 0 minutes | 60 minutes |

The Site Reliability Owner alerts at 50% of an RTO, declares an objective breach at the target, and preserves measured exercise/incident timestamps. These objectives require production provider configuration, backup validation, and completed recovery exercises; code and documentation alone do not prove achievement.
