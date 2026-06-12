-- Migration 0105: Backfill clean evaluation detection records
--
-- Root cause: the monitoring worker creates monitoring_evaluation_no_threat and
-- coverage_telemetry rows in the evidence table (health proofs) but historically
-- did NOT create corresponding rows in the detections table. The
-- evidence_package_without_detection_alert_incident_chain guard fired because
-- evidence_count > 0 && last_detection_at IS NULL, blocking LIVE status.
--
-- This migration is idempotent: it only inserts for workspaces that have at least
-- one monitoring_evaluation_no_threat evidence record AND zero rows in detections.
-- It creates exactly one clean detection per affected workspace, dated to the
-- observed_at of the most recent no-threat evaluation evidence row.
--
-- After this migration the guard condition (evidence without detection) is satisfied
-- for clean monitoring workspaces without requiring fake alerts or incidents.

DO $$
DECLARE
    rec RECORD;
BEGIN
    FOR rec IN
        SELECT DISTINCT ON (e.workspace_id)
            e.workspace_id,
            e.asset_id,
            e.target_id,
            e.monitored_system_id,
            e.observed_at
        FROM evidence e
        WHERE e.event_type = 'monitoring_evaluation_no_threat'
          AND NOT EXISTS (
              SELECT 1 FROM detections d
               WHERE d.workspace_id = e.workspace_id
          )
        ORDER BY e.workspace_id, e.observed_at DESC
    LOOP
        INSERT INTO detections (
            id,
            workspace_id,
            monitored_system_id,
            protected_asset_id,
            detection_type,
            severity,
            confidence,
            title,
            evidence_summary,
            evidence_source,
            source_rule,
            status,
            detected_at,
            raw_evidence_json,
            monitoring_run_id,
            linked_alert_id,
            created_at,
            updated_at
        ) VALUES (
            gen_random_uuid(),
            rec.workspace_id,
            rec.monitored_system_id,
            rec.asset_id,
            'monitoring_evaluation_no_threat',
            'none',
            1.0,
            'Monitoring evaluation: no threats detected',
            'Clean monitoring evaluation backfill. '
                'Live telemetry was received and evaluated; no anomalies were detected.',
            'live',
            NULL,
            'clean',
            rec.observed_at,
            '{"backfill": true, "migration": "0105"}'::jsonb,
            NULL,
            NULL,
            NOW(),
            NOW()
        );
    END LOOP;
END;
$$;
