-- 0157 — External Watchlist: founder-only, read-only monitoring of PUBLIC
-- blockchain infrastructure belonging to RWA protocols that are NOT (yet)
-- Decoda customers.
--
-- WHY A SEPARATE SET OF TABLES
--   An external protocol is not a customer. It has no organization, no
--   workspace, no members, no plan, and it has not authorized anything. Every
--   customer table (targets, telemetry_events, threat_detections, alerts,
--   incidents, response actions) is keyed by a NOT NULL workspace_id, and every
--   customer read path is scoped by it. Putting external rows there would mean
--   either inventing a fake tenant or loosening that key — both of which would
--   let public-watchlist data leak into a customer surface, or a customer
--   control (response actions, integrations, policy execution) reach an
--   external target. So external monitoring lives here, in tables that no
--   customer query reads, behind an explicit discriminator:
--
--       customer monitoring   monitoring_scope = 'workspace'        (implicit:
--                             every row carries its workspace_id)
--       external monitoring   monitoring_scope = 'external_public'  (enforced
--                             by a CHECK on every row below)
--
-- EXECUTION AUTHORITY
--   execution_authority is 'NONE' on every watchlist, target and finding, and a
--   CHECK constraint makes any other value unrepresentable. Nothing in this
--   schema can hold a key, a signature, a signer, a write permission, or an
--   approval. There is deliberately no column to put one in.
--
-- ADDRESSES
--   Stored normalised: '0x' + 40 lowercase hex characters, enforced by CHECK,
--   so a mixed-case EIP-55 spelling of the same address cannot create a second
--   target or a second event.
--
-- STATUS
--   external_watchlists.status is the last status the worker DERIVED from the
--   stored facts (poll, backfill, heartbeat). It is a snapshot for operators,
--   not the source of truth: the API re-derives status from the same facts at
--   read time, so a worker that stops running is reported as degraded rather
--   than as the last "live" it wrote.
--
-- SAFE ON EXISTING DATA
--   Additive only. No existing table is altered and no existing row is read or
--   rewritten. Every statement is idempotent so the startup migration runner can
--   re-apply it safely.

CREATE TABLE IF NOT EXISTS external_watchlists (
    id UUID PRIMARY KEY,
    name TEXT NOT NULL CHECK (char_length(btrim(name)) BETWEEN 1 AND 120),
    slug TEXT NOT NULL CHECK (slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$' AND char_length(slug) <= 140),
    website_url TEXT NULL CHECK (
        website_url IS NULL OR (website_url ~* '^https?://[^[:space:]]+$' AND char_length(website_url) <= 300)
    ),
    description TEXT NULL CHECK (description IS NULL OR char_length(description) <= 2000),
    status TEXT NOT NULL DEFAULT 'degraded'
        CHECK (status IN ('live', 'backfilling', 'paused', 'degraded', 'error')),
    monitoring_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    backfill_days INTEGER NOT NULL DEFAULT 30 CHECK (backfill_days IN (0, 7, 14, 30)),
    detection_profiles JSONB NOT NULL DEFAULT '[]'::jsonb
        CHECK (jsonb_typeof(detection_profiles) = 'array'),
    -- Per-protocol detection tuning (relative multiples, not absolute amounts).
    detection_config JSONB NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(detection_config) = 'object'),
    disclaimer_version TEXT NOT NULL,
    monitoring_scope TEXT NOT NULL DEFAULT 'external_public'
        CHECK (monitoring_scope = 'external_public'),
    execution_authority TEXT NOT NULL DEFAULT 'NONE'
        CHECK (execution_authority = 'NONE'),
    -- Set by the explicit founder "Convert to Pilot Workspace" action. It records
    -- that a Pilot workspace was CREATED; it does not record that the protocol
    -- authorized anything — that is established only by the workspace
    -- invitation flow, inside the customer workspace.
    converted_workspace_id UUID NULL REFERENCES workspaces(id) ON DELETE SET NULL,
    converted_at TIMESTAMPTZ NULL,
    created_by UUID NULL REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMPTZ NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_external_watchlists_slug_active
    ON external_watchlists (slug)
    WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_external_watchlists_created
    ON external_watchlists (created_at DESC)
    WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_external_watchlists_status
    ON external_watchlists (status)
    WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS external_watchlist_targets (
    id UUID PRIMARY KEY,
    watchlist_id UUID NOT NULL REFERENCES external_watchlists(id) ON DELETE CASCADE,
    target_type TEXT NOT NULL CHECK (target_type IN ('contract', 'wallet', 'multisig', 'oracle')),
    network TEXT NOT NULL CHECK (network ~ '^[a-z0-9]+(-[a-z0-9]+)*$' AND char_length(network) <= 40),
    chain_id BIGINT NOT NULL CHECK (chain_id > 0),
    address TEXT NOT NULL CHECK (
        address ~ '^0x[0-9a-f]{40}$'
        AND address <> '0x0000000000000000000000000000000000000000'
    ),
    label TEXT NULL CHECK (label IS NULL OR char_length(label) <= 120),
    abi_source TEXT NULL CHECK (abi_source IS NULL OR abi_source IN ('standard_events', 'verified_source', 'manual')),
    contract_type TEXT NULL CHECK (contract_type IS NULL OR char_length(contract_type) <= 64),
    monitoring_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    monitoring_scope TEXT NOT NULL DEFAULT 'external_public'
        CHECK (monitoring_scope = 'external_public'),
    execution_authority TEXT NOT NULL DEFAULT 'NONE'
        CHECK (execution_authority = 'NONE'),
    -- Validation facts recorded when the target was added / diagnosed.
    has_code BOOLEAN NULL,
    -- Runtime facts, kept separate on purpose (CLAUDE.md rule 3):
    --   last_polled_at           the loop TRIED this target (poll)
    --   last_successful_poll_at  the loop READ the chain for it (poll succeeded)
    --   last_event_at            on-chain data actually ARRIVED (telemetry)
    live_start_block BIGINT NULL CHECK (live_start_block IS NULL OR live_start_block >= 0),
    last_processed_block BIGINT NULL CHECK (last_processed_block IS NULL OR last_processed_block >= 0),
    last_polled_at TIMESTAMPTZ NULL,
    last_successful_poll_at TIMESTAMPTZ NULL,
    last_event_at TIMESTAMPTZ NULL,
    last_poll_error TEXT NULL CHECK (last_poll_error IS NULL OR char_length(last_poll_error) <= 500),
    consecutive_failures INTEGER NOT NULL DEFAULT 0 CHECK (consecutive_failures >= 0),
    -- Rule state (oracle last answer, Safe threshold, resolved aggregator).
    runtime_state JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(runtime_state) = 'object'),
    -- Live-tail lease so two worker replicas never poll the same target at once.
    lease_owner TEXT NULL,
    lease_expires_at TIMESTAMPTZ NULL,
    created_by UUID NULL REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Soft removal keeps every event, finding and evidence package attributable.
    removed_at TIMESTAMPTZ NULL
);

-- One active monitor per (protocol, chain, address). Removed rows do not block
-- re-adding the same address later.
CREATE UNIQUE INDEX IF NOT EXISTS idx_external_watchlist_targets_unique_active
    ON external_watchlist_targets (watchlist_id, chain_id, address)
    WHERE removed_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_external_watchlist_targets_chain_address
    ON external_watchlist_targets (chain_id, address)
    WHERE removed_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_external_watchlist_targets_watchlist
    ON external_watchlist_targets (watchlist_id, created_at)
    WHERE removed_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_external_watchlist_targets_poll_due
    ON external_watchlist_targets (monitoring_enabled, last_polled_at NULLS FIRST)
    WHERE removed_at IS NULL;

CREATE TABLE IF NOT EXISTS external_watchlist_backfills (
    id UUID PRIMARY KEY,
    watchlist_id UUID NOT NULL REFERENCES external_watchlists(id) ON DELETE CASCADE,
    target_id UUID NOT NULL REFERENCES external_watchlist_targets(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'running', 'completed', 'partial', 'failed')),
    requested_days INTEGER NOT NULL CHECK (requested_days IN (7, 14, 30)),
    requested_by UUID NULL REFERENCES users(id) ON DELETE SET NULL,
    network TEXT NOT NULL,
    chain_id BIGINT NOT NULL CHECK (chain_id > 0),
    -- Planning (done by the worker, never in an API request):
    --   window_start_at  now - requested_days, the time the scan reaches back to
    --   plan_ranges      [[from, to], ...] block ranges NOT already covered by an
    --                    earlier backfill of this target, so a re-run never
    --                    re-reads (or double counts) history it already has
    window_start_at TIMESTAMPTZ NULL,
    estimated_start_block BIGINT NULL,
    window_end_block BIGINT NULL,
    plan_ranges JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(plan_ranges) = 'array'),
    -- The scan's OWN rule state ({range_index, state}): configuration observed while
    -- replaying history. Kept apart from the target's live runtime_state so the past
    -- is never compared against, and never overwrites, the present.
    rule_state JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(rule_state) = 'object'),
    planned_at TIMESTAMPTZ NULL,
    -- Progress: ranges before range_index are fully scanned; the current range
    -- is scanned from its start through cursor_block.
    range_index INTEGER NOT NULL DEFAULT 0 CHECK (range_index >= 0),
    cursor_block BIGINT NULL,
    total_blocks BIGINT NOT NULL DEFAULT 0 CHECK (total_blocks >= 0),
    scanned_blocks BIGINT NOT NULL DEFAULT 0 CHECK (scanned_blocks >= 0),
    chunk_size INTEGER NULL CHECK (chunk_size IS NULL OR chunk_size > 0),
    chunks_completed INTEGER NOT NULL DEFAULT 0 CHECK (chunks_completed >= 0),
    retries INTEGER NOT NULL DEFAULT 0 CHECK (retries >= 0),
    logs_found INTEGER NOT NULL DEFAULT 0 CHECK (logs_found >= 0),
    events_inserted INTEGER NOT NULL DEFAULT 0 CHECK (events_inserted >= 0),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    last_error TEXT NULL CHECK (last_error IS NULL OR char_length(last_error) <= 500),
    status_reason TEXT NULL CHECK (status_reason IS NULL OR char_length(status_reason) <= 200),
    lease_owner TEXT NULL,
    lease_expires_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ NULL,
    CHECK (scanned_blocks <= total_blocks OR total_blocks = 0)
);

-- At most one open backfill per target, enforced by the database so two
-- concurrent "Run historical backfill" clicks cannot start two scans.
CREATE UNIQUE INDEX IF NOT EXISTS idx_external_watchlist_backfills_one_open
    ON external_watchlist_backfills (target_id)
    WHERE status IN ('pending', 'running');
CREATE INDEX IF NOT EXISTS idx_external_watchlist_backfills_claim
    ON external_watchlist_backfills (status, created_at);
CREATE INDEX IF NOT EXISTS idx_external_watchlist_backfills_watchlist
    ON external_watchlist_backfills (watchlist_id, created_at DESC);

CREATE TABLE IF NOT EXISTS external_watchlist_events (
    id UUID PRIMARY KEY,
    watchlist_id UUID NOT NULL REFERENCES external_watchlists(id) ON DELETE CASCADE,
    target_id UUID NOT NULL REFERENCES external_watchlist_targets(id) ON DELETE CASCADE,
    monitoring_scope TEXT NOT NULL DEFAULT 'external_public'
        CHECK (monitoring_scope = 'external_public'),
    network TEXT NOT NULL,
    chain_id BIGINT NOT NULL CHECK (chain_id > 0),
    -- The contract that EMITTED the log (for a wallet target this is the token).
    contract_address TEXT NOT NULL CHECK (contract_address ~ '^0x[0-9a-f]{40}$'),
    block_number BIGINT NOT NULL CHECK (block_number >= 0),
    block_hash TEXT NULL CHECK (block_hash IS NULL OR block_hash ~ '^0x[0-9a-f]{64}$'),
    tx_hash TEXT NOT NULL CHECK (tx_hash ~ '^0x[0-9a-f]{64}$'),
    log_index INTEGER NOT NULL CHECK (log_index >= 0),
    event_name TEXT NOT NULL,
    event_type TEXT NOT NULL,
    event_category TEXT NOT NULL CHECK (event_category IN (
        'access_control', 'upgradeability', 'emergency_control',
        'token_operation', 'multisig', 'oracle'
    )),
    topic0 TEXT NOT NULL CHECK (topic0 ~ '^0x[0-9a-f]{64}$'),
    decoded JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(decoded) = 'object'),
    decode_status TEXT NOT NULL CHECK (decode_status IN ('decoded', 'partial', 'undecoded')),
    initiator TEXT NULL CHECK (initiator IS NULL OR initiator ~ '^0x[0-9a-f]{40}$'),
    -- The public log exactly as the provider returned it (topics, data, ids).
    raw_log JSONB NOT NULL DEFAULT '{}'::jsonb,
    payload_sha256 TEXT NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
    -- How the row arrived. Both are real chain data; neither is a simulator.
    ingest_source TEXT NOT NULL CHECK (ingest_source IN ('historical_backfill', 'live_poll')),
    backfill_id UUID NULL REFERENCES external_watchlist_backfills(id) ON DELETE SET NULL,
    block_timestamp TIMESTAMPTZ NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    observed_at_source TEXT NOT NULL
        CHECK (observed_at_source IN ('block_timestamp', 'estimated_from_block', 'ingested_at')),
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- One row per on-chain log per target: a re-scan of the same range can
    -- never create duplicate telemetry.
    UNIQUE (target_id, tx_hash, log_index)
);

CREATE INDEX IF NOT EXISTS idx_external_watchlist_events_watchlist_observed
    ON external_watchlist_events (watchlist_id, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_external_watchlist_events_target_block
    ON external_watchlist_events (target_id, block_number DESC);
CREATE INDEX IF NOT EXISTS idx_external_watchlist_events_watchlist_category
    ON external_watchlist_events (watchlist_id, event_category, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_external_watchlist_events_tx
    ON external_watchlist_events (tx_hash);

-- Rolling baselines for relative (not absolute) thresholds. One row per
-- (target, token/feed, statistic). Updated in the same transaction that
-- advances the scan cursor, so a crash never double counts a transfer.
CREATE TABLE IF NOT EXISTS external_watchlist_baselines (
    target_id UUID NOT NULL REFERENCES external_watchlist_targets(id) ON DELETE CASCADE,
    token_address TEXT NOT NULL CHECK (token_address ~ '^0x[0-9a-f]{40}$'),
    stat_kind TEXT NOT NULL CHECK (stat_kind IN ('transfer', 'mint_burn', 'oracle_interval')),
    sample_count BIGINT NOT NULL DEFAULT 0 CHECK (sample_count >= 0),
    mean NUMERIC NOT NULL DEFAULT 0,
    max_value NUMERIC NULL,
    updated_block BIGINT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (target_id, token_address, stat_kind)
);

CREATE TABLE IF NOT EXISTS external_watchlist_findings (
    id UUID PRIMARY KEY,
    watchlist_id UUID NOT NULL REFERENCES external_watchlists(id) ON DELETE CASCADE,
    target_id UUID NULL REFERENCES external_watchlist_targets(id) ON DELETE SET NULL,
    monitoring_scope TEXT NOT NULL DEFAULT 'external_public'
        CHECK (monitoring_scope = 'external_public'),
    execution_authority TEXT NOT NULL DEFAULT 'NONE'
        CHECK (execution_authority = 'NONE'),
    detector_version TEXT NOT NULL,
    rule_key TEXT NOT NULL,
    detection_profile TEXT NOT NULL,
    finding_type TEXT NOT NULL,
    -- Careful, non-accusatory vocabulary only. There is no class for "attack",
    -- "exploit" or "compromise": public telemetry cannot establish intent.
    finding_class TEXT NOT NULL CHECK (finding_class IN (
        'review', 'administrative_change', 'observed_anomaly',
        'unusual_activity', 'privileged_configuration_change'
    )),
    title TEXT NOT NULL CHECK (char_length(title) <= 200),
    severity TEXT NOT NULL CHECK (severity IN ('low', 'medium', 'high')),
    confidence NUMERIC(4, 3) NOT NULL DEFAULT 0 CHECK (confidence >= 0 AND confidence <= 1),
    status TEXT NOT NULL DEFAULT 'new' CHECK (status IN (
        'new', 'reviewed', 'expected', 'interesting', 'outreach_candidate', 'dismissed'
    )),
    network TEXT NOT NULL,
    chain_id BIGINT NOT NULL CHECK (chain_id > 0),
    contract_address TEXT NULL CHECK (contract_address IS NULL OR contract_address ~ '^0x[0-9a-f]{40}$'),
    tx_hash TEXT NULL CHECK (tx_hash IS NULL OR tx_hash ~ '^0x[0-9a-f]{64}$'),
    block_number BIGINT NULL CHECK (block_number IS NULL OR block_number >= 0),
    log_index INTEGER NULL,
    observed_at TIMESTAMPTZ NULL,
    initiator TEXT NULL CHECK (initiator IS NULL OR initiator ~ '^0x[0-9a-f]{40}$'),
    primary_event_id UUID NULL REFERENCES external_watchlist_events(id) ON DELETE SET NULL,
    related_event_ids JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(related_event_ids) = 'array'),
    decoded JSONB NOT NULL DEFAULT '{}'::jsonb,
    previous_state JSONB NULL,
    new_state JSONB NULL,
    explanation TEXT NOT NULL,
    -- {observed_fact, decoda_interpretation, operational_authorization, source}
    ai_analysis JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(ai_analysis) = 'object'),
    -- Internal scoring inputs. Never included in a prospect report.
    score_inputs JSONB NOT NULL DEFAULT '{}'::jsonb,
    dedupe_key TEXT NOT NULL,
    status_note TEXT NULL CHECK (status_note IS NULL OR char_length(status_note) <= 1000),
    status_updated_by UUID NULL REFERENCES users(id) ON DELETE SET NULL,
    status_updated_at TIMESTAMPTZ NULL,
    detected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (watchlist_id, dedupe_key)
);

CREATE INDEX IF NOT EXISTS idx_external_watchlist_findings_watchlist_detected
    ON external_watchlist_findings (watchlist_id, detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_external_watchlist_findings_watchlist_status
    ON external_watchlist_findings (watchlist_id, status, detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_external_watchlist_findings_watchlist_observed
    ON external_watchlist_findings (watchlist_id, observed_at DESC);

CREATE TABLE IF NOT EXISTS external_watchlist_evidence (
    id UUID PRIMARY KEY,
    watchlist_id UUID NOT NULL REFERENCES external_watchlists(id) ON DELETE CASCADE,
    finding_id UUID NULL REFERENCES external_watchlist_findings(id) ON DELETE SET NULL,
    package_type TEXT NOT NULL CHECK (package_type IN ('evidence_package', 'prospect_report')),
    monitoring_scope TEXT NOT NULL DEFAULT 'external_public'
        CHECK (monitoring_scope = 'external_public'),
    -- For a prospect report: the evidence package whose verification it cites.
    source_evidence_id UUID NULL REFERENCES external_watchlist_evidence(id) ON DELETE SET NULL,
    manifest JSONB NOT NULL,
    seal JSONB NOT NULL,
    files JSONB NOT NULL,
    manifest_sha256 TEXT NOT NULL CHECK (manifest_sha256 ~ '^[0-9a-f]{64}$'),
    evidence_sha256 TEXT NOT NULL CHECK (evidence_sha256 ~ '^[0-9a-f]{64}$'),
    signature_algorithm TEXT NULL,
    disclaimer_version TEXT NOT NULL,
    generated_by UUID NULL REFERENCES users(id) ON DELETE SET NULL,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_external_watchlist_evidence_watchlist
    ON external_watchlist_evidence (watchlist_id, generated_at DESC);
CREATE INDEX IF NOT EXISTS idx_external_watchlist_evidence_finding
    ON external_watchlist_evidence (finding_id, generated_at DESC)
    WHERE finding_id IS NOT NULL;

-- Provenance of an explicit founder conversion. One per watchlist, so a
-- double-click can never provision two Pilot workspaces for one protocol.
CREATE TABLE IF NOT EXISTS external_watchlist_conversions (
    id UUID PRIMARY KEY,
    watchlist_id UUID NOT NULL UNIQUE REFERENCES external_watchlists(id) ON DELETE RESTRICT,
    organization_id UUID NULL,
    workspace_id UUID NULL REFERENCES workspaces(id) ON DELETE SET NULL,
    copied_targets JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(copied_targets) = 'array'),
    evaluation_days INTEGER NOT NULL CHECK (evaluation_days > 0),
    evaluation_expires_at TIMESTAMPTZ NULL,
    converted_by UUID NULL REFERENCES users(id) ON DELETE SET NULL,
    converted_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'organizations'
    ) THEN
        -- 0150 has not run yet; the column stays unconstrained until it does.
        RETURN;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'external_watchlist_conversions_organization_fk'
    ) THEN
        ALTER TABLE external_watchlist_conversions
            ADD CONSTRAINT external_watchlist_conversions_organization_fk
            FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE SET NULL;
    END IF;
END $$;

-- Worker liveness (heartbeat), separate from per-target poll facts.
CREATE TABLE IF NOT EXISTS external_watchlist_worker_state (
    worker_name TEXT PRIMARY KEY,
    heartbeat_at TIMESTAMPTZ NOT NULL,
    last_cycle_at TIMESTAMPTZ NULL,
    last_completed_at TIMESTAMPTZ NULL,
    consecutive_failures INTEGER NOT NULL DEFAULT 0 CHECK (consecutive_failures >= 0),
    last_cycle_summary JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Evidence integrity: observed telemetry and sealed evidence packages are
-- append-only. A row can be removed (with its protocol), never rewritten, so a
-- package that cites an event can be re-verified against the same bytes later.
CREATE OR REPLACE FUNCTION external_watchlist_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS external_watchlist_events_append_only ON external_watchlist_events;
CREATE TRIGGER external_watchlist_events_append_only
    BEFORE UPDATE ON external_watchlist_events
    FOR EACH ROW EXECUTE FUNCTION external_watchlist_append_only();

DROP TRIGGER IF EXISTS external_watchlist_evidence_append_only ON external_watchlist_evidence;
CREATE TRIGGER external_watchlist_evidence_append_only
    BEFORE UPDATE ON external_watchlist_evidence
    FOR EACH ROW EXECUTE FUNCTION external_watchlist_append_only();
