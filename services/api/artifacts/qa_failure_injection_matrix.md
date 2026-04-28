# QA Failure-Injection Matrix Report

Owner: QA engineer  
Date: 2026-04-28

## Scope
Automated checks now cover previously reported failure modes **B-F** and one additional hidden chain-integrity condition.

## Results

| Criterion | Failure mode | Automated coverage | Status |
|---|---|---|---|
| B | DB degradation | `test_failure_injection_db_degradation_and_partial_query_failure` validates `runtime_degraded_reason=partial_query_failure` and field-level optional table fallback reason codes. | PASS |
| C | Provider unreachable | `test_failure_injection_provider_unreachable_sets_degraded_reason` validates runtime enters degraded mode with `degraded_reason=provider_unreachable`. | PASS |
| D | Partial query failure | Covered alongside B by injected query failure path and degraded status reason assertions. | PASS |
| E | Stale telemetry | `test_failure_injection_stale_telemetry_downgrades_freshness` validates stale/unavailable telemetry freshness is surfaced consistently. | PASS |
| F | Partial endpoint failures + stale snapshot retention (frontend) | `threat-operations-partial-endpoint-stale-retention-source.spec.ts` validates failed endpoint normalization plus stale cache retention path. | PASS |
| Hidden | Chain-integrity contradiction: response action without incident | `test_chain_integrity_hidden_problem_flags_action_without_incident` validates contradiction flag propagation. | PASS |

## Notes
- Frontend assertions confirm stale-safe collection handling keeps prior cached rows visible during partial endpoint failures.
- Backend assertions validate degraded reason, reason-codes, and contradiction integrity signals used by runtime status and operator UI.
