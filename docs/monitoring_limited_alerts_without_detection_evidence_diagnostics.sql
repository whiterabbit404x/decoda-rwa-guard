-- ============================================================================
-- monitoring_status=limited  /  status_reason=alerts_without_detection_evidence
--   READ-ONLY diagnostics + product-taxonomy note
-- ============================================================================
-- Context (Base Mainnet production, commit bd026f5).
--
-- The QuickNode Stream coverage-refresh fix is working. Production emits:
--
--     quicknode_stream_coverage_refresh health_status=healthy targets_eligible=1
--         coverage_refreshed=1 refresh_interval_seconds=150 matched=0
--     reporting_systems=1 fresh_live_reporting_systems=1 replay_only_systems=0
--     chosen_evidence_source=live source_of_evidence=live
--     realtime_ingestion_status=healthy realtime_live_coverage_fresh=True
--     fallback_rpc_degraded=True
--
-- …and the workspace rollup still ends at:
--
--     monitoring_runtime_status_decision decision=limited
--     status_reason=alerts_without_detection_evidence
--
-- That is NOT a Stream regression. `limited` here is produced by a SEPARATE,
-- ADDITIVE proof-chain contradiction, on this exact path in
-- services/api/app/monitoring_runner.py:
--
--   1. count_open_alerts  (~line 8768)
--        raw_open_alerts_count            = every alert whose status is
--                                           open / acknowledged / investigating
--        open_alerts (canonical)          = alerts -> detection_events
--                                                  -> telemetry_events
--        legacy_open_alerts_row (legacy)  = alerts -> detections, where the
--                                           detection has raw_evidence_json
--                                           or a detection_evidence row
--        open_alerts_without_evidence_count =
--            MIN(raw - canonical, raw - legacy)          <- most generous count
--
--   2. ~line 10586   open_alerts_without_evidence_count > 0
--                      -> proof_chain_status = 'incomplete'
--                      -> runtime_status_summary = 'degraded'
--                      -> proof_chain_missing_reason_codes +=
--                         'alerts_without_canonical_detection_event'
--
--   3. ~line 11036/11037  contradiction_conditions add BOTH
--                         'open_alerts_without_detection_evidence' and
--                         'alert_without_detection'
--
--   4. ~line 11083   contradiction_reason_overrides['alert_without_detection']
--                      == ('degraded', 'alerts_without_detection_evidence')
--                    Flags are iterated in SORTED order and the FIRST flag with
--                    an override wins the reason token. 'alert_without_detection'
--                    sorts before 'open_alerts_without_detection_evidence' and
--                    'proof_chain_link_missing', and it is the only one of the
--                    three that HAS an override — hence the exact production
--                    token `alerts_without_detection_evidence`.
--
--   5. ~line 11153   contradiction_severity == 'degraded'
--                      -> runtime_status = 'degraded'
--                      -> monitoring_status = 'limited'
--                    ('alert_without_detection' is NOT in `hard_contradictions`,
--                     so the workspace correctly does NOT go offline.)
--
--   6. workspace_monitoring_summary._normalized_monitoring_status independently
--      returns 'limited' because `contradiction_flags` is non-empty.
--
-- PRODUCT TAXONOMY — WHY `limited` IS THE INTENDED OUTCOME
-- ----------------------------------------------------------------------------
-- `monitoring_status` is a three-value workspace ROLLUP: live | limited | offline.
-- `live` is only reachable with an EMPTY contradiction set. An open alert that
-- satisfies neither the canonical nor the legacy proof chain is an alert a
-- customer can open and Decoda cannot prove — exporting evidence for it would
-- produce nothing. Claiming "Live / Fresh / Verified" over such an alert is the
-- overclaim CLAUDE.md forbids ("No alert must not be shown as healthy", "Keep
-- customer-facing status labels truthful and fail-closed"). So this combination
-- IS intentionally mapped to LIMITED:
--
--     realtime ingestion health      = healthy    (concept 1)
--     monitoring coverage health     = healthy    (concept 2)
--     fallback RPC health            = degraded   (concept 3)
--     historical evidence integrity  = FAILING    (concept 4)  <- forces limited
--     current security alert state   = 1 open     (concept 5)
--
-- The four concepts stay separately named and DO NOT overwrite one another: the
-- integrity contradiction degrades the rollup only. It leaves evidence_source,
-- reporting_systems, fresh_live_reporting_systems, replay_only_systems and
-- realtime_ingestion.* byte-for-byte identical to the same workspace with no
-- orphan alert. That separation is locked by
-- services/api/tests/test_monitoring_status_evidence_integrity_separation.py.
--
-- AGE IS DELIBERATELY NOT A FACTOR. The counter is bounded by alert STATUS, not
-- by created_at: an unprovable alert that is still OPEN is an unresolved
-- integrity failure however old it is. Resolving/closing the alert (or restoring
-- its evidence link) clears the flag; the passage of time does not.
--
-- WHAT THIS FILE IS FOR
-- ----------------------------------------------------------------------------
-- The runtime payload reports only the COUNT
-- (`open_alerts_without_detection_evidence`). Block A–E below name the exact
-- rows so an operator can classify them:
--
--     A. legitimate current production integrity problem
--     B. historical record predating the current detection/evidence model
--     C. demo / fixture data
--     D. orphan / inconsistent record
--     E. false positive from status aggregation
--
-- Block E is the one to read first: some alert lanes structurally CANNOT satisfy
-- either proof-chain join, because they are not chain-telemetry detections at all
-- (asset-risk findings carry their evidence in asset_risk_findings.evidence;
-- analysis-run alerts carry theirs in analysis_runs.response_payload). If the
-- offending rows are all in those lanes, the finding is a taxonomy gap in the
-- counter, not a customer-visible integrity failure — see the note under E.
--
-- SAFETY
--   * Every statement here is a SELECT. Nothing writes, nothing auto-runs, and
--     this file is NOT a migration — do not add it to services/api/migrations/.
--   * No free-text columns (payload, summary, title) are selected, so no
--     customer content, address, tx hash or secret leaves the database.
--   * Set :ws to the workspace uuid, e.g.
--         psql "$DATABASE_URL" -v ws="'00000000-0000-0000-0000-000000000000'" \
--              -f docs/monitoring_limited_alerts_without_detection_evidence_diagnostics.sql
-- ============================================================================


-- ---------------------------------------------------------------------------
-- A. Reproduce the counter arithmetic exactly as monitoring_runner.py computes it
-- ---------------------------------------------------------------------------
-- Expect: alerts_without_evidence_effective > 0 (that is what forces `limited`).
WITH raw_open AS (
    SELECT COUNT(*) AS c
    FROM alerts
    WHERE workspace_id = :ws::uuid
      AND status IN ('open', 'acknowledged', 'investigating')
),
canonical_linked AS (
    SELECT COUNT(*) AS c
    FROM alerts a
    JOIN detection_events de
      ON de.workspace_id = a.workspace_id
     AND de.id = a.detection_event_id
    JOIN telemetry_events te
      ON te.workspace_id = de.workspace_id
     AND te.id = de.telemetry_event_id
    WHERE a.workspace_id = :ws::uuid
      AND a.status IN ('open', 'acknowledged', 'investigating')
),
legacy_linked AS (
    SELECT COUNT(DISTINCT a.id) AS c
    FROM alerts a
    JOIN detections d
      ON (d.id = a.detection_id OR d.linked_alert_id = a.id)
     AND d.workspace_id = a.workspace_id
    WHERE a.workspace_id = :ws::uuid
      AND a.status IN ('open', 'acknowledged', 'investigating')
      AND (
        d.raw_evidence_json IS NOT NULL
        OR EXISTS (
            SELECT 1 FROM detection_evidence de2
            WHERE de2.workspace_id = d.workspace_id AND de2.detection_id = d.id
        )
      )
)
SELECT
    raw_open.c                                             AS raw_open_alerts,
    canonical_linked.c                                     AS canonical_evidence_linked,
    legacy_linked.c                                        AS legacy_evidence_linked,
    GREATEST(raw_open.c - canonical_linked.c, 0)           AS gap_canonical,
    GREATEST(raw_open.c - legacy_linked.c, 0)              AS gap_legacy,
    LEAST(
        GREATEST(raw_open.c - canonical_linked.c, 0),
        GREATEST(raw_open.c - legacy_linked.c, 0)
    )                                                      AS alerts_without_evidence_effective
FROM raw_open, canonical_linked, legacy_linked;


-- ---------------------------------------------------------------------------
-- B. Name the offending rows: open alerts provable by NEITHER chain
-- ---------------------------------------------------------------------------
-- These are the rows driving status_reason=alerts_without_detection_evidence.
-- Structured identity + lineage only — no free text.
SELECT
    a.id                                                   AS alert_id,
    a.created_at,
    a.status,
    a.severity,
    a.alert_type,
    a.module_key,
    a.source,
    a.source_service,
    a.target_id,
    (a.detection_event_id IS NOT NULL)                     AS has_detection_event_id,
    (a.detection_id       IS NOT NULL)                     AS has_detection_id,
    (a.analysis_run_id    IS NOT NULL)                     AS has_analysis_run_id,
    EXTRACT(DAY FROM (NOW() - a.created_at))::int          AS age_days
FROM alerts a
WHERE a.workspace_id = :ws::uuid
  AND a.status IN ('open', 'acknowledged', 'investigating')
  AND NOT EXISTS (
        SELECT 1
        FROM detection_events de
        JOIN telemetry_events te
          ON te.workspace_id = de.workspace_id AND te.id = de.telemetry_event_id
        WHERE de.workspace_id = a.workspace_id AND de.id = a.detection_event_id
  )
  AND NOT EXISTS (
        SELECT 1
        FROM detections d
        WHERE d.workspace_id = a.workspace_id
          AND (d.id = a.detection_id OR d.linked_alert_id = a.id)
          AND (
            d.raw_evidence_json IS NOT NULL
            OR EXISTS (
                SELECT 1 FROM detection_evidence de2
                WHERE de2.workspace_id = d.workspace_id AND de2.detection_id = d.id
            )
          )
  )
ORDER BY a.created_at ASC;


-- ---------------------------------------------------------------------------
-- C. Which link is missing, per offending alert (why it fails each chain)
-- ---------------------------------------------------------------------------
SELECT
    a.id                                                   AS alert_id,
    a.created_at,
    a.alert_type,
    a.module_key,
    CASE
        WHEN a.detection_event_id IS NULL              THEN 'no_detection_event_id'
        WHEN de.id IS NULL                             THEN 'detection_event_row_missing'
        WHEN de.telemetry_event_id IS NULL             THEN 'detection_event_has_no_telemetry_event_id'
        WHEN te.id IS NULL                             THEN 'telemetry_event_row_missing'
        ELSE 'canonical_chain_ok'
    END                                                    AS canonical_break,
    CASE
        WHEN d.id IS NULL                              THEN 'no_linked_detection_row'
        WHEN d.raw_evidence_json IS NULL
             AND NOT EXISTS (
                 SELECT 1 FROM detection_evidence de2
                 WHERE de2.workspace_id = d.workspace_id AND de2.detection_id = d.id
             )                                         THEN 'detection_has_no_evidence'
        ELSE 'legacy_chain_ok'
    END                                                    AS legacy_break
FROM alerts a
LEFT JOIN detection_events de
       ON de.workspace_id = a.workspace_id AND de.id = a.detection_event_id
LEFT JOIN telemetry_events te
       ON te.workspace_id = de.workspace_id AND te.id = de.telemetry_event_id
LEFT JOIN detections d
       ON d.workspace_id = a.workspace_id
      AND (d.id = a.detection_id OR d.linked_alert_id = a.id)
WHERE a.workspace_id = :ws::uuid
  AND a.status IN ('open', 'acknowledged', 'investigating')
ORDER BY a.created_at ASC;


-- ---------------------------------------------------------------------------
-- D. Age / era distribution — separates B (historical) from A (current)
-- ---------------------------------------------------------------------------
-- If every offending row predates the workspace's oldest canonical
-- detection_event, they are pre-canonical-model records (classification B).
-- If any row is newer, the CURRENT pipeline is emitting unprovable alerts
-- (classification A) and that is a live defect to chase, not history.
SELECT
    (SELECT MIN(created_at) FROM detection_events WHERE workspace_id = :ws::uuid)
                                                           AS first_canonical_detection_event_at,
    (SELECT MIN(observed_at) FROM telemetry_events WHERE workspace_id = :ws::uuid)
                                                           AS first_telemetry_event_at,
    MIN(a.created_at)                                      AS oldest_offending_alert_at,
    MAX(a.created_at)                                      AS newest_offending_alert_at,
    COUNT(*)                                               AS offending_alerts,
    COUNT(*) FILTER (
        WHERE a.created_at >= COALESCE(
            (SELECT MIN(created_at) FROM detection_events WHERE workspace_id = :ws::uuid),
            'infinity'::timestamptz)
    )                                                      AS offending_after_canonical_model
FROM alerts a
WHERE a.workspace_id = :ws::uuid
  AND a.status IN ('open', 'acknowledged', 'investigating')
  AND a.detection_event_id IS NULL
  AND a.detection_id IS NULL;


-- ---------------------------------------------------------------------------
-- E. Lane classification — is this a real integrity failure or a counter gap?
-- ---------------------------------------------------------------------------
-- Some alert lanes NEVER write detection_event_id / detection_id, by design:
--
--   module_key='asset_risk' / source='asset_risk_assessor'
--       services/api/app/domains/asset_risk/service.py — evidence lives in
--       asset_risk_findings.evidence and alerts.payload->'evidence'.
--   analysis_run_id IS NOT NULL, detection_* NULL
--       pilot.maybe_insert_alert — evidence lives in
--       analysis_runs.response_payload.
--
-- Rows in those lanes are legitimate product records with a DIFFERENT evidence
-- origin. If `offending_with_own_evidence` accounts for the whole gap, the
-- finding is classification E (aggregation false positive): the counter is
-- demanding chain-detection evidence from alerts that were never supposed to
-- have any. THAT is the case where the mapping should be narrowed to the
-- chain-detection lanes — a fail-closed change, since any alert not provably in
-- an alternate-evidence lane keeps counting.
--
-- If instead the gap is wallet-transfer / threat-detection / proof-chain alerts,
-- it is classification A or D and `limited` is fully correct: fix or resolve the
-- rows, do not touch the status code.
SELECT
    COUNT(*)                                               AS offending_total,
    COUNT(*) FILTER (WHERE a.module_key = 'asset_risk'
                        OR a.source = 'asset_risk_assessor'
                        OR a.source_service = 'asset-risk-assessor')
                                                           AS lane_asset_risk,
    COUNT(*) FILTER (WHERE a.analysis_run_id IS NOT NULL)  AS lane_analysis_run,
    COUNT(*) FILTER (WHERE a.module_key = 'strategic_infrastructure_guard'
                        OR a.alert_type ILIKE '%wallet_transfer%')
                                                           AS lane_wallet_transfer,
    COUNT(*) FILTER (WHERE a.alert_type = 'monitoring_proof_chain')
                                                           AS lane_proof_chain,
    COUNT(*) FILTER (
        WHERE a.module_key = 'asset_risk'
           OR a.source = 'asset_risk_assessor'
           OR a.source_service = 'asset-risk-assessor'
           OR a.analysis_run_id IS NOT NULL
    )                                                      AS offending_with_own_evidence,
    COUNT(*) FILTER (
        WHERE a.module_key IS DISTINCT FROM 'asset_risk'
          AND a.source IS DISTINCT FROM 'asset_risk_assessor'
          AND a.source_service IS DISTINCT FROM 'asset-risk-assessor'
          AND a.analysis_run_id IS NULL
    )                                                      AS offending_requiring_chain_evidence
FROM alerts a
WHERE a.workspace_id = :ws::uuid
  AND a.status IN ('open', 'acknowledged', 'investigating')
  AND a.detection_event_id IS NULL
  AND a.detection_id IS NULL;


-- ---------------------------------------------------------------------------
-- F. Is the integrity flag the ONLY thing holding the workspace at `limited`?
-- ---------------------------------------------------------------------------
-- The reason token is first-wins over SORTED contradiction flags, so
-- `alerts_without_detection_evidence` can mask other conditions. These are the
-- other contradiction counters the same rollup reads; all must be 0 for the
-- workspace to reach `live` once the alerts are resolved.
SELECT
    (SELECT COUNT(*) FROM incidents i
      WHERE i.workspace_id = :ws::uuid
        AND i.status IN ('open', 'acknowledged')
        AND NOT EXISTS (SELECT 1 FROM alerts a
                         WHERE a.workspace_id = i.workspace_id
                           AND (a.incident_id = i.id OR i.source_alert_id = a.id)))
                                                           AS incidents_without_alert,
    (SELECT COUNT(*) FROM response_actions ra
      WHERE ra.workspace_id = :ws::uuid AND ra.incident_id IS NULL)
                                                           AS response_actions_without_incident,
    (SELECT COUNT(*) FROM targets t
      WHERE t.workspace_id = :ws::uuid AND t.deleted_at IS NULL AND t.enabled = TRUE)
                                                           AS enabled_targets,
    (SELECT MAX(observed_at) FROM telemetry_events
      WHERE workspace_id = :ws::uuid)                      AS last_telemetry_at,
    (SELECT MAX(created_at) FROM detection_events
      WHERE workspace_id = :ws::uuid)                      AS last_detection_event_at;


-- ============================================================================
-- NO REPAIR BLOCK IS PROVIDED ON PURPOSE.
--
-- Resolving these rows is a product decision, not a schema repair:
--   * classification A / D — repair the linkage or resolve the alert through the
--     normal workflow. services/api/scripts/repair_live_rpc_proof_chain.py
--     already archives orphan open proof-chain alerts and rebuilds both chains;
--     run it with DRY_RUN=1 first and review its plan.
--   * classification B / C — close the alerts through the product (status
--     'resolved'), which clears the flag truthfully. Do NOT delete rows and do
--     NOT rewrite detection linkage to manufacture evidence that never existed;
--     that would put fabricated proof in front of a customer.
--   * classification E — do not touch the data. Narrow the counter in
--     monitoring_runner.py to the lanes that are supposed to carry chain
--     detection evidence, and extend
--     services/api/tests/test_monitoring_status_evidence_integrity_separation.py.
-- ============================================================================
