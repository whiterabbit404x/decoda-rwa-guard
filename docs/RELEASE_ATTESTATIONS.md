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

The creator refuses to overwrite an existing directory. The attestation records the deployed Git commit, immutable container image reference, deployment identifier, CI run identifier, production environment, approved independent penetration-test evidence reference, collection time, and the SHA-256 digest of the complete runtime evidence. `signature.json` contains an Ed25519 signature and public key for offline verification.

## Strict evidence contract

The workflow obtains one JSON document from the protected `production` environment. Evidence is rejected unless it is fresh, strict, commit-bound, immutable-image-bound, deployment-bound, and CI-run-bound. Staging and production-like aliases are not release-authoritative. Every item must have a passing result and an evidence identifier.

Required runtime probes:

- deployed commit SHA;
- deployed immutable image reference with the exact `sha256` digest;
- database migration head with zero pending migrations;
- healthy worker heartbeat;
- persistent Redis/Redis Streams event-bus round trip;
- a cross-tenant denial probe covering at least two tenants;
- live-provider freshness within the declared threshold;
- a verified live billing transaction and webhook receipt;
- alert p95 latency within the declared threshold;
- restored backup with integrity verification;
- completed failover within its RTO target; and
- delivered email notification with a verified provider receipt.

Required security gates are dependency scanning, secret scanning, static analysis, and infrastructure policy validation. Missing or failed gates reject the release.

`safe_to_sell_broadly_today` is copied into an attestation only after all strict checks pass and an approved HTTPS evidence URI or security ticket for independent penetration testing is embedded in the signed payload. Local, CI-only, demo, fallback, stale, partial, or non-strict evidence cannot produce that claim.

## Signing key

Configure `RELEASE_ATTESTATION_SIGNING_KEY` in the selected protected GitHub environment as base64 for exactly 32 Ed25519 private-key bytes. Also configure `RELEASE_PROBE_URL` and `RELEASE_PROBE_TOKEN`. The probe endpoint must honor the expected commit, immutable image reference, deployment, and CI-run headers and return the complete schema-version-2 evidence document; the validator independently checks those values rather than trusting the request alone.

Run the workflow manually against the deployment being attested. GitHub artifact upload uses a commit-and-deployment-specific name, disables overwrite, and fails if the signed bundle is absent.

## Dispatch inputs and required secrets

Dispatch the workflow only after production reports the immutable deployment identity. The required inputs are `deployment_id`, `environment=production`, `image_ref` in `<registry>/<image>@sha256:<64 lowercase hex>` form, and `penetration_test_evidence` as an approved HTTPS evidence URI or security ticket. The protected `production` GitHub environment must provide `RELEASE_PROBE_URL`, `RELEASE_PROBE_TOKEN`, and `RELEASE_ATTESTATION_SIGNING_KEY`. Secret values must never be committed or copied into the attestation bundle.

The production probe receives `X-Expected-Commit-SHA`, `X-Expected-Image-Ref`, `X-Deployment-ID`, and `X-CI-Run-ID`. Its response must independently report the same commit, image reference, deployment, and CI run. A mismatch at either the top-level identity or the deployed-image runtime probe rejects attestation creation. Offline verification likewise requires the expected commit, deployment ID, and image reference.
