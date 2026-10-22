-- Decoda product events (Decoda Assets, Decoda Vault) and the security signals Guard
-- reports back to them — additive only.
--
--   * decoda_product_links: an organization admin routes one producer to ONE
--     workspace of the organization. The admin who links is the user recorded
--     on the alerts and incidents Guard creates from that producer's events
--     (alerts.user_id / incidents.user_id are NOT NULL), as the threat engine
--     records an asset's creator.
--   * decoda_product_events: every signed event accepted from a producer, kept
--     as received. An event for a known organization that has no link for its
--     producer is kept as `unrouted` (no detection; counted on Integrations).
--     These are testnet product events: they are NEVER written to telemetry,
--     detection or monitoring tables and never count towards live monitoring
--     health or reporting systems.
--   * decoda_product_signals: the latest state Guard reports to each product for
--     one external object (alert, incident, coverage, risk), with its delivery
--     state. A changed state is a new version, delivered with a new event id.
--
-- Rollback = redeploy the previous release: it never reads these tables. To
-- remove them: DROP the three tables and DELETE the schema_migrations row.

CREATE TABLE IF NOT EXISTS decoda_product_links (
    id UUID PRIMARY KEY,
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    producer TEXT NOT NULL CHECK (producer IN ('decoda-assets', 'decoda-vault')),
    created_by_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_decoda_product_links_producer
    ON decoda_product_links (organization_id, producer);
CREATE INDEX IF NOT EXISTS idx_decoda_product_links_workspace
    ON decoda_product_links (workspace_id);

CREATE TABLE IF NOT EXISTS decoda_product_events (
    id UUID PRIMARY KEY,
    producer TEXT NOT NULL CHECK (producer IN ('decoda-assets', 'decoda-vault')),
    event_id TEXT NOT NULL CHECK (length(event_id) BETWEEN 8 AND 128),
    event_type TEXT NOT NULL CHECK (length(event_type) BETWEEN 3 AND 120),
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    workspace_id UUID NULL REFERENCES workspaces(id) ON DELETE SET NULL,
    routing_status TEXT NOT NULL CHECK (routing_status IN ('routed', 'unrouted')),
    -- The producer's own organization id (Assets org / Vault org): where signals go back to.
    product_tenant_ref TEXT NULL CHECK (product_tenant_ref IS NULL OR length(product_tenant_ref) <= 128),
    severity_hint TEXT NULL,
    synthetic BOOLEAN NOT NULL DEFAULT FALSE,
    envelope JSONB NOT NULL,
    detection TEXT NOT NULL DEFAULT 'none'
        CHECK (detection IN ('none', 'recorded', 'alert', 'alert_and_incident', 'coverage')),
    alert_id UUID NULL,
    incident_id UUID NULL,
    occurred_at TIMESTAMPTZ NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_decoda_product_events_event
    ON decoda_product_events (producer, event_id);
CREATE INDEX IF NOT EXISTS idx_decoda_product_events_organization
    ON decoda_product_events (organization_id, producer, received_at DESC);
CREATE INDEX IF NOT EXISTS idx_decoda_product_events_workspace
    ON decoda_product_events (workspace_id, received_at DESC) WHERE workspace_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS decoda_product_signals (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    destination TEXT NOT NULL CHECK (destination IN ('decoda-assets', 'decoda-vault')),
    kind TEXT NOT NULL CHECK (kind IN ('alert', 'incident', 'coverage', 'risk')),
    external_id TEXT NOT NULL CHECK (length(external_id) BETWEEN 1 AND 128),
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    body JSONB NOT NULL,
    body_hash TEXT NOT NULL,
    delivered_hash TEXT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'delivered', 'failed', 'dead', 'not_configured')),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    lease_owner TEXT NULL,
    lease_expires_at TIMESTAMPTZ NULL,
    last_error TEXT NULL CHECK (last_error IS NULL OR length(last_error) <= 500),
    last_http_status INTEGER NULL,
    related_event_id TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    delivered_at TIMESTAMPTZ NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_decoda_product_signals_object
    ON decoda_product_signals (workspace_id, destination, kind, external_id);
CREATE INDEX IF NOT EXISTS idx_decoda_product_signals_due
    ON decoda_product_signals (next_attempt_at) WHERE status IN ('pending', 'failed', 'not_configured');
