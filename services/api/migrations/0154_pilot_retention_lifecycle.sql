-- 0154 — Pilot data-retention and deletion lifecycle.
--
-- WHAT THIS EXISTS TO FIX
--   Migration 0095/0096 built a real deletion engine (workspace_retention_policies
--   → data_deletion_requests → data_retention.execute_request → a durable,
--   leased, retry-safe worker). It performs real hard deletes and real
--   anonymisation, and it removes object-storage artifacts. What it never had is
--   a POLICY: schedule_requests only sweeps a workspace that has a row in
--   workspace_retention_policies with enabled = TRUE, and nothing in the product
--   ever wrote one. Every Pilot workspace therefore retained everything forever
--   while the Privacy page said "until contractual retention ends".
--
--   Two facts were also missing from organizations: WHEN a Pilot ended, and WHEN
--   the data it produced becomes eligible for deletion. Without them the end of a
--   Pilot changed entitlements and nothing else.
--
-- WHAT IT ADDS
--   organizations.pilot_ended_at            the recorded end of the evaluation
--   organizations.pilot_grace_ends_at       end of the read/export grace window
--   organizations.pilot_end_reason          why it ended (founder text / 'evaluation_expired')
--   organizations.pilot_ended_by_user_id    the internal admin who ended it, when there was one
--   workspace_retention_policies.effective_from   when a seeded policy starts sweeping
--   workspace_retention_policies.source           'pilot_default' vs 'workspace'
--   'alerts' as a retention data class (alerts carry findings in this schema)
--   'pilot_end_purge' as a deletion request type
--
-- WHAT IS SAFE ON EXISTING DATA
--   Forward-only and additive. NOTHING is deleted by this migration, and no
--   deletion is scheduled by it either.
--
--   The policy backfill seeds Pilot defaults for existing PILOT workspaces only,
--   with ON CONFLICT DO NOTHING so a workspace whose owner already configured
--   retention keeps exactly what they configured. Every seeded row carries
--   effective_from = NOW() + 30 days: the age-based sweep cannot touch a single
--   pre-existing record until that date, which is what makes deploying this
--   migration non-destructive rather than a bulk delete.
--
--   Scale and Enterprise workspaces are deliberately NOT seeded. Their retention
--   is a contract term, and inventing a Pilot-length period for a paying tenant
--   would be exactly the silent retention decision this change exists to remove.
--
-- HOW ALREADY-ENDED PILOTS ARE TREATED
--   pilot_ended_at is backfilled ONLY from a fact the database already records:
--   an evaluation_expires_at that is in the past. That is the real end moment, so
--   it is not a guess.
--
--   An organization that is status = 'expired' with NO evaluation_expires_at has
--   no recorded end date. This migration does NOT invent one — pilot_ended_at
--   stays NULL, no grace window starts, and no deletion is ever scheduled for it.
--   The founder console reports it as "Pilot end date not recorded"; ending the
--   Pilot explicitly is what starts the clock.
--
--   For the rows that ARE backfilled, pilot_grace_ends_at is NOW() + 30 days, not
--   the historical expiry + 30 days. These customers were never given a deletion
--   schedule, so the grace window starts when the policy does. A Pilot that
--   expired a year ago therefore gets a full 30-day export window from this
--   deployment, and nothing becomes deletable on deploy day.

-- ── organizations: the end-of-Pilot facts ────────────────────────────────────
ALTER TABLE organizations
    ADD COLUMN IF NOT EXISTS pilot_ended_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS pilot_grace_ends_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS pilot_end_reason TEXT NULL,
    ADD COLUMN IF NOT EXISTS pilot_ended_by_user_id UUID NULL;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables
               WHERE table_schema = 'public' AND table_name = 'users')
       AND NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'organizations_pilot_ended_by_fk')
    THEN
        ALTER TABLE organizations
            ADD CONSTRAINT organizations_pilot_ended_by_fk
            FOREIGN KEY (pilot_ended_by_user_id) REFERENCES users(id) ON DELETE SET NULL;
    END IF;
END $$;

-- The worker's due-selection scan: Pilots whose grace window has closed.
CREATE INDEX IF NOT EXISTS idx_organizations_pilot_grace
    ON organizations (pilot_grace_ends_at)
    WHERE pilot_ended_at IS NOT NULL;

-- ── retention policy rows: when they take effect, and who set them ───────────
ALTER TABLE workspace_retention_policies
    ADD COLUMN IF NOT EXISTS effective_from TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'workspace';

-- 'pilot_default' is a period Decoda chose and published; 'workspace' is one the
-- customer set. The settings screen must be able to say which, because a seeded
-- default presented as the customer's own configuration would be untrue.
ALTER TABLE workspace_retention_policies
    DROP CONSTRAINT IF EXISTS workspace_retention_policies_source_check;
ALTER TABLE workspace_retention_policies
    ADD CONSTRAINT workspace_retention_policies_source_check
    CHECK (source IN ('pilot_default', 'workspace'));

-- ── 'alerts' becomes a first-class retention data class ──────────────────────
-- Findings are stored in `alerts` in this schema (finding_actions.finding_id and
-- finding_decisions.finding_id both reference alerts.id), so without this class
-- every alert and every finding a Pilot produced was outside the deletion engine
-- entirely.
ALTER TABLE workspace_retention_policies
    DROP CONSTRAINT IF EXISTS workspace_retention_policies_data_class_check;
ALTER TABLE workspace_retention_policies
    ADD CONSTRAINT workspace_retention_policies_data_class_check
    CHECK (data_class IN ('telemetry','detections','alerts','incidents','audit_logs','exports','user_data'));

ALTER TABLE retention_external_artifacts
    DROP CONSTRAINT IF EXISTS retention_external_artifacts_data_class_check;
ALTER TABLE retention_external_artifacts
    ADD CONSTRAINT retention_external_artifacts_data_class_check
    CHECK (data_class IN ('telemetry','detections','alerts','incidents','audit_logs','exports','user_data'));

-- ── 'pilot_end_purge' becomes a distinguishable request type ─────────────────
-- A scheduled end-of-Pilot purge is not an age-based sweep and not a customer
-- request; the console and the audit trail have to be able to tell them apart.
ALTER TABLE data_deletion_requests
    DROP CONSTRAINT IF EXISTS data_deletion_requests_request_type_check;
ALTER TABLE data_deletion_requests
    ADD CONSTRAINT data_deletion_requests_request_type_check
    CHECK (request_type IN ('retention_sweep','workspace_data','user_data','pilot_end_purge'));

-- ── backfill 1: record the end of Pilots the database can already prove ──────
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.tables
                   WHERE table_schema = 'public' AND table_name = 'organizations') THEN
        RETURN;
    END IF;

    UPDATE organizations
    SET pilot_ended_at = evaluation_expires_at,
        -- Forward-looking on purpose: see "HOW ALREADY-ENDED PILOTS ARE TREATED".
        pilot_grace_ends_at = NOW() + INTERVAL '30 days',
        pilot_end_reason = COALESCE(pilot_end_reason, 'evaluation_expired'),
        updated_at = NOW()
    WHERE plan = 'pilot'
      AND pilot_ended_at IS NULL
      AND evaluation_expires_at IS NOT NULL
      AND evaluation_expires_at < NOW();
END $$;

-- ── backfill 2: seed the Pilot default retention policy ──────────────────────
-- Values mirror services/api/app/pilot_retention.py PILOT_RETENTION_DAYS /
-- PILOT_DELETION_MODES. That module is the source of truth; this literal list
-- exists because a migration cannot import Python, and the test
-- test_pilot_retention_lifecycle.py::test_migration_defaults_match_the_module
-- fails if the two ever drift.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.tables
                   WHERE table_schema = 'public' AND table_name = 'workspace_retention_policies') THEN
        RETURN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_schema = 'public' AND table_name = 'workspaces'
                     AND column_name = 'organization_id') THEN
        -- 0150 has not run: there is no plan to read, so nothing is seeded. The
        -- application seeds each workspace on its next provisioning path.
        RETURN;
    END IF;

    INSERT INTO workspace_retention_policies
        (workspace_id, data_class, retention_days, deletion_mode, enabled, source, effective_from,
         updated_by_user_id, created_at, updated_at)
    SELECT w.id, d.data_class, d.retention_days, d.deletion_mode, TRUE, 'pilot_default',
           NOW() + INTERVAL '30 days', NULL, NOW(), NOW()
    FROM workspaces w
    JOIN organizations o ON o.id = w.organization_id
    CROSS JOIN (VALUES
        ('telemetry',   90,  'hard_delete'),
        ('detections',  180, 'hard_delete'),
        ('alerts',      180, 'hard_delete'),
        ('incidents',   365, 'hard_delete'),
        ('exports',     365, 'hard_delete'),
        ('audit_logs',  365, 'anonymize'),
        ('user_data',   30,  'anonymize')
    ) AS d(data_class, retention_days, deletion_mode)
    WHERE o.plan = 'pilot'
    ON CONFLICT (workspace_id, data_class) DO NOTHING;
END $$;
