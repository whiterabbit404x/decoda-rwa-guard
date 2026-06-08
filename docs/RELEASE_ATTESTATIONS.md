# Authoritative release attestations

The only release-authoritative automation is `.github/workflows/release-attestation.yml`.
Legacy proof generators are diagnostic tools and their mutable `artifacts/*/latest` outputs are not release claims.

## Immutable identity

A successful run writes exactly one bundle at:

```text
artifacts/release-attestations/<40-character-commit-sha>/<deployment-id>/
  attestation.json
  signature.json
```

The creator refuses to overwrite an existing directory. The attestation records the deployed Git commit, deployment identifier, CI run identifier, production-like environment, collection time, and the SHA-256 digest of the complete runtime evidence. `signature.json` contains an Ed25519 signature and public key for offline verification.

## Strict evidence contract

The workflow obtains one JSON document from one protected production-like environment. Evidence is rejected unless it is fresh, strict, commit-bound, deployment-bound, and CI-run-bound. Every item must have a passing result and an evidence identifier.

Required runtime probes:

- deployed commit SHA;
- database migration head with zero pending migrations;
- healthy worker heartbeat;
- persistent Redis/Redis Streams event-bus round trip;
- a cross-tenant denial probe covering at least two tenants;
- provider freshness within the declared threshold;
- alert p95 latency within the declared threshold;
- restored backup with integrity verification;
- completed failover within its RTO target; and
- delivered notification with a provider receipt.

Required security gates are dependency scanning, secret scanning, static analysis, and infrastructure policy validation. Missing or failed gates reject the release.

`safe_to_sell_broadly_today` is copied into an attestation only after all strict checks pass. Local, CI-only, demo, fallback, stale, partial, or non-strict evidence cannot produce that claim.

## Signing key

Configure `RELEASE_ATTESTATION_SIGNING_KEY` in the selected protected GitHub environment as base64 for exactly 32 Ed25519 private-key bytes. Also configure `RELEASE_PROBE_URL` and `RELEASE_PROBE_TOKEN`. The probe endpoint must honor the expected commit, deployment, and CI-run headers and return the complete evidence document; the validator independently checks those values rather than trusting the request alone.

Run the workflow manually against the deployment being attested. GitHub artifact upload uses a commit-and-deployment-specific name, disables overwrite, and fails if the signed bundle is absent.
