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
-- …and the workspace rollup still ended at:
--
--     monitoring_runtime_status_decision decision=limited
--     status_reason=alerts_without_detection_evidence
--
-- That is NOT a Stream regression. `limited` here is produced by a SEPARATE,
-- ADDITIVE proof-chain contradiction, on this exact path in
-- services/api/app/monitoring_runner.py:
--
--   1. count_open_alerts_without_evidence
--        raw_open_alerts_count = every alert whose status is
--                                open / acknowledged / investigating
--        open_alerts_without_evidence_count =
--            COUNT(open alerts matching NO evidence home)
--        open_alerts_with_either_chain_count =
--            COUNT(open alerts matching ANY evidence home)
--      Both come from ONE aggregate pass embedding the shared canonical
--      predicate monitoring_runner.OPEN_ALERT_EVIDENCE_PROVABLE_SQL.
--
--   2. open_alerts_without_evidence_count > 0
--        -> proof_chain_status = 'incomplete'
--        -> runtime_status_summary = 'degraded'
--        -> proof_chain_missing_reason_codes += 'alerts_without_canonical_detection_event'
--
--   3. contradiction_conditions add BOTH
--        'open_alerts_without_detection_evidence' and 'alert_without_detection'
--
--   4. contradiction_reason_overrides['alert_without_detection']
--        == ('degraded', 'alerts_without_detection_evidence')
--      Flags are iterated in SORTED order and the FIRST flag with an override
--      wins the reason token. 'alert_without_detection' sorts before
--      'open_alerts_without_detection_evidence' and 'proof_chain_link_missing',
--      and it is the only one of the three that HAS an override — hence the
--      exact production token `alerts_without_detection_evidence`.
--
--   5. contradiction_severity == 'degraded'
--        -> runtime_status = 'degraded'
--        -> monitoring_status = 'limited'
--      ('alert_without_detection' is NOT in `hard_contradictions`, so the
--       workspace correctly does NOT go offline.)
--
--   6. workspace_monitoring_summary._normalized_monitoring_status independently
--      returns 'limited' because `contradiction_flags` is non-empty. A SINGLE
--      flag is enough — including `proof_chain_link_missing`, which is raised
--      from its own chain count (chain_open_alerts_count). That count therefore
--      reads the same provable set as the anti-join; otherwise clearing
--      'alert_without_detection' just moves the workspace to `limited` under a
--      different reason token.
--
-- THE FIVE CANONICAL EVIDENCE HOMES
-- ----------------------------------------------------------------------------
-- An open alert is PROVABLE when a real evidence-bearing ROW exists in any of:
--
--   1. alerts.detection_event_id -> detection_events -> telemetry_events
--      canonical lane; create_alert_from_detection_event (pilot.py).
--   2. alerts.detection_id / detections.linked_alert_id -> detections carrying
--      raw_evidence_json or a detection_evidence row; legacy lane, written by
--      _upsert_alert (QuickNode wallet-transfer path) and monitoring_proof_chain.
--   3. asset_risk_findings.alert_id -> asset_risk_findings.evidence
--      domains/asset_risk/service.reconcile_findings.
--   4. threat_detections.linked_alert_id -> threat_detection_evidence
--      domains/threat_detection/service.ensure_alert_for_detection.
--   5. alerts.analysis_run_id -> analysis_runs.response_payload
--      pilot.maybe_insert_alert.
--
-- Lanes 3-5 shipped after the counter was written, so alerts raised into them
-- were reported as unprovable while carrying genuine evidence. Recognizing them
-- is NOT a loosened threshold — every lane still demands an evidence-bearing
-- row, and a LABEL IS NOT EVIDENCE:
--
--   * module_key ('asset_risk', 'threat_detection'), source, source_service and
--     alert_type prove nothing and appear in NO lane below.
--   * asset_risk_findings.evidence, analysis_runs.response_payload and
--     threat_detection_evidence.evidence_payload are all
--     `JSONB NOT NULL DEFAULT '{}'::jsonb`, so `IS NOT NULL` is true of every
--     row. Emptiness — not nullability — is the test.
--   * Simulator-sourced threat detections never prove anything: simulator data
--     must never be presented as customer evidence.
--
-- PRODUCT TAXONOMY — WHEN `limited` IS THE INTENDED OUTCOME
-- ----------------------------------------------------------------------------
-- `monitoring_status` is a three-value workspace ROLLUP: live | limited | offline.
-- `live` is only reachable with an EMPTY contradiction set. An open alert that
-- satisfies NO evidence home is an alert a customer can open and Decoda cannot
-- prove — exporting evidence for it would produce nothing. Claiming
-- "Live / Fresh / Verified" over such an alert is the overclaim CLAUDE.md
-- forbids ("No alert must not be shown as healthy", "Keep customer-facing status
-- labels truthful and fail-closed"). So that combination IS intentionally
-- mapped to LIMITED:
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
-- services/api/tests/test_monitoring_status_evidence_integrity_separation.py,
-- and the evidence-home taxonomy by
-- services/api/tests/test_monitoring_alert_evidence_home_taxonomy.py.
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
-- Blocks A and E are the ones to read first: an alert lane that structurally
-- cannot satisfy either CHAIN join is not automatically an orphan — it may hold
-- real evidence in home 3, 4 or 5. Block A separates "provable by a chain",
-- "provable only by a non-chain home", and "provable by nothing at all".
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
-- A. Reproduce the counter — chain-only vs. all five evidence homes
-- ---------------------------------------------------------------------------
-- READ THIS BLOCK FIRST. It answers the only question that decides whether the
-- production `limited` is real.
--
--   alerts_without_evidence_chain_only  = open alerts matching NEITHER chain lane
--       -- the definition before the asset-risk / threat-detection / analysis-run
--          evidence homes were recognized.
--
--   alerts_without_evidence_all_homes   = open alerts matching NO evidence home
--       -- what monitoring_runner.py computes now, and what the runtime payload
--          reports as `open_alerts_without_detection_evidence`.
--
--   taxonomy_gap_false_positives        = chain_only - all_homes
--       -- alerts the chain-only definition called unprovable that in fact carry
--          real evidence, just not in a chain lane. These are FALSE POSITIVES;
--          no data repair clears them and none should be attempted.
--
-- HOW TO READ THE RESULT
--   taxonomy_gap_false_positives > 0 AND alerts_without_evidence_all_homes = 0
--       -> the production `limited` was entirely the counter's taxonomy gap.
--          No data problem, no cleanup. Blocks B-E return nothing.
--   alerts_without_evidence_all_homes > 0
--       -> there are genuinely unprovable open alerts. `limited` is CORRECT and
--          must stay. Run Blocks B-E to classify those rows.
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
),
either_chain_linked AS (
    -- |canonical UNION legacy| — the chain half of the definition.
    SELECT COUNT(*) AS c
    FROM alerts a
    WHERE a.workspace_id = :ws::uuid
      AND a.status IN ('open', 'acknowledged', 'investigating')
      AND (
        EXISTS (
            SELECT 1
            FROM detection_events de
            JOIN telemetry_events te
              ON te.workspace_id = de.workspace_id
             AND te.id = de.telemetry_event_id
            WHERE de.workspace_id = a.workspace_id
              AND de.id = a.detection_event_id
        )
        OR EXISTS (
            SELECT 1
            FROM detections d
            WHERE d.workspace_id = a.workspace_id
              AND (d.id = a.detection_id OR d.linked_alert_id = a.id)
              AND (
                d.raw_evidence_json IS NOT NULL
                OR EXISTS (
                    SELECT 1 FROM detection_evidence de3
                    WHERE de3.workspace_id = d.workspace_id AND de3.detection_id = d.id
                )
              )
        )
      )
),
any_home_linked AS (
    -- All five evidence homes — the canonical provability definition.
    SELECT COUNT(*) AS c
    FROM alerts a
    WHERE a.workspace_id = :ws::uuid
      AND a.status IN ('open', 'acknowledged', 'investigating')
      AND (
        EXISTS (
            SELECT 1
            FROM detection_events de
            JOIN telemetry_events te
              ON te.workspace_id = de.workspace_id
             AND te.id = de.telemetry_event_id
            WHERE de.workspace_id = a.workspace_id
              AND de.id = a.detection_event_id
        )
        OR EXISTS (
            SELECT 1
            FROM detections d
            WHERE d.workspace_id = a.workspace_id
              AND (d.id = a.detection_id OR d.linked_alert_id = a.id)
              AND (
                d.raw_evidence_json IS NOT NULL
                OR EXISTS (
                    SELECT 1 FROM detection_evidence de4
                    WHERE de4.workspace_id = d.workspace_id AND de4.detection_id = d.id
                )
              )
        )
        OR EXISTS (
            SELECT 1
            FROM asset_risk_findings f
            WHERE f.workspace_id = a.workspace_id
              AND f.alert_id = a.id
              AND f.evidence IS NOT NULL
              AND jsonb_typeof(f.evidence) <> 'null'
              AND f.evidence <> '{}'::jsonb
              AND f.evidence <> '[]'::jsonb
        )
        OR EXISTS (
            SELECT 1
            FROM threat_detections td
            WHERE td.workspace_id = a.workspace_id
              AND td.linked_alert_id = a.id
              AND td.evidence_source <> 'simulator'
              AND EXISTS (
                  SELECT 1
                  FROM threat_detection_evidence tde
                  WHERE tde.workspace_id = td.workspace_id
                    AND tde.detection_id = td.id
                    AND (
                      tde.telemetry_id IS NOT NULL
                      OR (
                        tde.evidence_payload IS NOT NULL
                        AND jsonb_typeof(tde.evidence_payload) <> 'null'
                        AND tde.evidence_payload <> '{}'::jsonb
                        AND tde.evidence_payload <> '[]'::jsonb
                      )
                    )
              )
        )
        OR EXISTS (
            SELECT 1
            FROM analysis_runs ar
            WHERE ar.id = a.analysis_run_id
              AND ar.workspace_id = a.workspace_id
              AND ar.response_payload IS NOT NULL
              AND jsonb_typeof(ar.response_payload) <> 'null'
              AND ar.response_payload <> '{}'::jsonb
              AND ar.response_payload <> '[]'::jsonb
        )
      )
)
SELECT
    raw_open.c                                             AS raw_open_alerts,
    canonical_linked.c                                     AS canonical_evidence_linked,
    legacy_linked.c                                        AS legacy_evidence_linked,
    either_chain_linked.c                                  AS either_chain_linked,
    any_home_linked.c                                      AS any_evidence_home_linked,
    GREATEST(raw_open.c - either_chain_linked.c, 0)        AS alerts_without_evidence_chain_only,
    GREATEST(raw_open.c - any_home_linked.c, 0)            AS alerts_without_evidence_all_homes,
    GREATEST(any_home_linked.c - either_chain_linked.c, 0) AS taxonomy_gap_false_positives
FROM raw_open, canonical_linked, legacy_linked, either_chain_linked, any_home_linked;


-- ---------------------------------------------------------------------------
-- B. Name the offending rows: open alerts provable by NO evidence home
-- ---------------------------------------------------------------------------
-- These, and only these, drive status_reason=alerts_without_detection_evidence.
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
    EXISTS (SELECT 1 FROM asset_risk_findings f
             WHERE f.workspace_id = a.workspace_id AND f.alert_id = a.id)
                                                           AS has_asset_risk_finding_row,
    EXISTS (SELECT 1 FROM threat_detections td
             WHERE td.workspace_id = a.workspace_id AND td.linked_alert_id = a.id)
                                                           AS has_threat_detection_row,
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
  AND NOT EXISTS (
        SELECT 1
        FROM asset_risk_findings f
        WHERE f.workspace_id = a.workspace_id
          AND f.alert_id = a.id
          AND f.evidence IS NOT NULL
          AND jsonb_typeof(f.evidence) <> 'null'
          AND f.evidence <> '{}'::jsonb
          AND f.evidence <> '[]'::jsonb
  )
  AND NOT EXISTS (
        SELECT 1
        FROM threat_detections td
        WHERE td.workspace_id = a.workspace_id
          AND td.linked_alert_id = a.id
          AND td.evidence_source <> 'simulator'
          AND EXISTS (
              SELECT 1
              FROM threat_detection_evidence tde
              WHERE tde.workspace_id = td.workspace_id
                AND tde.detection_id = td.id
                AND (
                  tde.telemetry_id IS NOT NULL
                  OR (
                    tde.evidence_payload IS NOT NULL
                    AND jsonb_typeof(tde.evidence_payload) <> 'null'
                    AND tde.evidence_payload <> '{}'::jsonb
                    AND tde.evidence_payload <> '[]'::jsonb
                  )
                )
          )
  )
  AND NOT EXISTS (
        SELECT 1
        FROM analysis_runs ar
        WHERE ar.id = a.analysis_run_id
          AND ar.workspace_id = a.workspace_id
          AND ar.response_payload IS NOT NULL
          AND jsonb_typeof(ar.response_payload) <> 'null'
          AND ar.response_payload <> '{}'::jsonb
          AND ar.response_payload <> '[]'::jsonb
  )
ORDER BY a.created_at ASC;


-- ---------------------------------------------------------------------------
-- C. Which link is missing, per open alert (why it fails each evidence home)
-- ---------------------------------------------------------------------------
-- Read `*_break` per row. A row is a genuine orphan only when EVERY column
-- reports a break; an `*_ok` in any column means the alert is provable and must
-- NOT be treated as an orphan.
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
    END                                                    AS legacy_break,
    CASE
        WHEN NOT EXISTS (SELECT 1 FROM asset_risk_findings f
                          WHERE f.workspace_id = a.workspace_id AND f.alert_id = a.id)
                                                       THEN 'no_asset_risk_finding_row'
        WHEN NOT EXISTS (SELECT 1 FROM asset_risk_findings f
                          WHERE f.workspace_id = a.workspace_id AND f.alert_id = a.id
                            AND f.evidence IS NOT NULL
                            AND jsonb_typeof(f.evidence) <> 'null'
                            AND f.evidence <> '{}'::jsonb
                            AND f.evidence <> '[]'::jsonb)
                                                       THEN 'asset_risk_finding_evidence_empty'
        ELSE 'asset_risk_evidence_ok'
    END                                                    AS asset_risk_break,
    CASE
        WHEN NOT EXISTS (SELECT 1 FROM threat_detections td
                          WHERE td.workspace_id = a.workspace_id AND td.linked_alert_id = a.id)
                                                       THEN 'no_threat_detection_row'
        WHEN NOT EXISTS (SELECT 1 FROM threat_detections td
                          WHERE td.workspace_id = a.workspace_id AND td.linked_alert_id = a.id
                            AND td.evidence_source <> 'simulator')
                                                       THEN 'threat_detection_evidence_is_simulator'
        WHEN NOT EXISTS (SELECT 1 FROM threat_detections td
                          JOIN threat_detection_evidence tde
                            ON tde.workspace_id = td.workspace_id AND tde.detection_id = td.id
                          WHERE td.workspace_id = a.workspace_id AND td.linked_alert_id = a.id
                            AND td.evidence_source <> 'simulator')
                                                       THEN 'threat_detection_has_no_evidence_rows'
        ELSE 'threat_detection_evidence_ok'
    END                                                    AS threat_detection_break,
    CASE
        WHEN a.analysis_run_id IS NULL                 THEN 'no_analysis_run_id'
        WHEN ar.id IS NULL                             THEN 'analysis_run_row_missing'
        WHEN ar.response_payload IS NULL
             OR jsonb_typeof(ar.response_payload) = 'null'
             OR ar.response_payload = '{}'::jsonb
             OR ar.response_payload = '[]'::jsonb      THEN 'analysis_run_response_payload_empty'
        ELSE 'analysis_run_evidence_ok'
    END                                                    AS analysis_run_break
FROM alerts a
LEFT JOIN detection_events de
       ON de.workspace_id = a.workspace_id AND de.id = a.detection_event_id
LEFT JOIN telemetry_events te
       ON te.workspace_id = de.workspace_id AND te.id = de.telemetry_event_id
LEFT JOIN detections d
       ON d.workspace_id = a.workspace_id
      AND (d.id = a.detection_id OR d.linked_alert_id = a.id)
LEFT JOIN analysis_runs ar
       ON ar.workspace_id = a.workspace_id AND ar.id = a.analysis_run_id
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
--
-- Scoped to alerts with NO linkage of any kind, so an alert carrying real
-- asset-risk / threat-detection / analysis-run evidence is not counted here.
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
  AND a.detection_id IS NULL
  AND a.analysis_run_id IS NULL
  AND NOT EXISTS (SELECT 1 FROM asset_risk_findings f
                   WHERE f.workspace_id = a.workspace_id AND f.alert_id = a.id)
  AND NOT EXISTS (SELECT 1 FROM threat_detections td
                   WHERE td.workspace_id = a.workspace_id AND td.linked_alert_id = a.id);


-- ---------------------------------------------------------------------------
-- E. Lane classification — is this a real integrity failure or a counter gap?
-- ---------------------------------------------------------------------------
-- Classification is by EVIDENCE ROW, never by label. module_key='asset_risk' or
-- 'threat_detection' on its own is not proof of anything and never clears an
-- alert here; only a real, non-empty evidence row does. The label columns are
-- reported separately, purely so an operator can see when a lane label and its
-- evidence disagree — `mislabelled_without_evidence` is exactly that case, and
-- those rows ARE genuine orphans.
--
-- HOW TO READ THE RESULT
--   provable_by_non_chain_home > 0 AND unprovable_total = 0
--       -> classification E (aggregation false positive). The alerts carry real
--          evidence in home 3/4/5. DO NOT touch the data; the corrected counter
--          in monitoring_runner.py already recognizes them.
--   unprovable_total > 0
--       -> classification A / B / C / D. `limited` is correct. Use Block C to see
--          which link is missing and Block D to date the rows.
SELECT
    COUNT(*)                                               AS open_alerts_total,
    COUNT(*) FILTER (WHERE ev.provable_by_chain)           AS provable_by_chain,
    COUNT(*) FILTER (WHERE NOT ev.provable_by_chain AND ev.provable_by_non_chain)
                                                           AS provable_by_non_chain_home,
    COUNT(*) FILTER (WHERE NOT ev.provable_by_chain AND NOT ev.provable_by_non_chain)
                                                           AS unprovable_total,
    -- Labels, reported but never trusted.
    COUNT(*) FILTER (WHERE ev.asset_risk_labelled)         AS label_asset_risk,
    COUNT(*) FILTER (WHERE ev.threat_labelled)             AS label_threat_detection,
    COUNT(*) FILTER (WHERE a.analysis_run_id IS NOT NULL)  AS label_analysis_run,
    COUNT(*) FILTER (
        WHERE (ev.asset_risk_labelled OR ev.threat_labelled)
          AND NOT ev.provable_by_chain
          AND NOT ev.provable_by_non_chain
    )                                                      AS mislabelled_without_evidence
FROM alerts a
CROSS JOIN LATERAL (
    SELECT
        (a.module_key = 'asset_risk'
         OR a.source = 'asset_risk_assessor'
         OR a.source_service = 'asset-risk-assessor')      AS asset_risk_labelled,
        (a.module_key = 'threat_detection'
         OR a.source = 'threat_detection_engineer'
         OR a.source_service = 'threat-detection-engineer') AS threat_labelled,
        (
            EXISTS (
                SELECT 1
                FROM detection_events de
                JOIN telemetry_events te
                  ON te.workspace_id = de.workspace_id AND te.id = de.telemetry_event_id
                WHERE de.workspace_id = a.workspace_id AND de.id = a.detection_event_id
            )
            OR EXISTS (
                SELECT 1
                FROM detections d
                WHERE d.workspace_id = a.workspace_id
                  AND (d.id = a.detection_id OR d.linked_alert_id = a.id)
                  AND (
                    d.raw_evidence_json IS NOT NULL
                    OR EXISTS (
                        SELECT 1 FROM detection_evidence de5
                        WHERE de5.workspace_id = d.workspace_id AND de5.detection_id = d.id
                    )
                  )
            )
        )                                                  AS provable_by_chain,
        (
            EXISTS (
                SELECT 1
                FROM asset_risk_findings f
                WHERE f.workspace_id = a.workspace_id
                  AND f.alert_id = a.id
                  AND f.evidence IS NOT NULL
                  AND jsonb_typeof(f.evidence) <> 'null'
                  AND f.evidence <> '{}'::jsonb
                  AND f.evidence <> '[]'::jsonb
            )
            OR EXISTS (
                SELECT 1
                FROM threat_detections td
                WHERE td.workspace_id = a.workspace_id
                  AND td.linked_alert_id = a.id
                  AND td.evidence_source <> 'simulator'
                  AND EXISTS (
                      SELECT 1
                      FROM threat_detection_evidence tde
                      WHERE tde.workspace_id = td.workspace_id
                        AND tde.detection_id = td.id
                        AND (
                          tde.telemetry_id IS NOT NULL
                          OR (
                            tde.evidence_payload IS NOT NULL
                            AND jsonb_typeof(tde.evidence_payload) <> 'null'
                            AND tde.evidence_payload <> '{}'::jsonb
                            AND tde.evidence_payload <> '[]'::jsonb
                          )
                        )
                  )
            )
            OR EXISTS (
                SELECT 1
                FROM analysis_runs ar
                WHERE ar.id = a.analysis_run_id
                  AND ar.workspace_id = a.workspace_id
                  AND ar.response_payload IS NOT NULL
                  AND jsonb_typeof(ar.response_payload) <> 'null'
                  AND ar.response_payload <> '{}'::jsonb
                  AND ar.response_payload <> '[]'::jsonb
            )
        )                                                  AS provable_by_non_chain
) ev
WHERE a.workspace_id = :ws::uuid
  AND a.status IN ('open', 'acknowledged', 'investigating');


-- ---------------------------------------------------------------------------
-- F. Is the integrity flag the ONLY thing holding the workspace at `limited`?
-- ---------------------------------------------------------------------------
-- The reason token is first-wins over SORTED contradiction flags, so
-- `alerts_without_detection_evidence` can mask other conditions, and ANY single
-- contradiction flag keeps monitoring_status at `limited`. These are the other
-- contradiction counters the same rollup reads; all must be 0 for the workspace
-- to reach `live` once the alerts are provable.
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
--     normal workflow.
--   * classification B / C — close the alerts through the product (status
--     'resolved'), which clears the flag truthfully. Do NOT delete rows and do
--     NOT rewrite detection linkage to manufacture evidence that never existed;
--     that would put fabricated proof in front of a customer.
--   * classification E — do not touch the data. The counter in
--     monitoring_runner.py already recognizes all five evidence homes
--     (OPEN_ALERT_EVIDENCE_PROVABLE_SQL); a row landing here means the evidence
--     is real and the alert is provable.
--
-- ON services/api/scripts/repair_live_rpc_proof_chain.py — READ BEFORE RUNNING.
--   Its orphan predicate is the CHAIN half only, and even narrower than Block A's
--   chain lanes: it keys on `alerts.detection_id` alone (ignoring
--   detections.linked_alert_id) and requires a detection_evidence row (ignoring
--   raw_evidence_json). Against the corrected definition it therefore resolves
--   alerts that ARE provable — every asset-risk, threat-detection and
--   analysis-run alert included. Run Block B first and only act on rows Block B
--   actually returns; use DRY_RUN=1 and review its plan.
-- ============================================================================
