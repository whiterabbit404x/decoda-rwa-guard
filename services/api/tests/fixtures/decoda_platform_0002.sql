-- VENDORED TEST FIXTURE — do not edit here.
-- Source: decoda-website platform/migrations/0002_legacy_organization_links.sql
-- (the Decoda platform owns this schema). Loaded after decoda_platform_0001.sql.

-- Decoda Platform — reviewed links between legacy product organizations and
-- platform organizations. Additive only.
--
-- A product that existed before the shared identity (RWA Guard today) has its
-- own organizations. When an operator imports a reviewed legacy manifest
-- (`npm run platform:import-legacy -- --apply`), every legacy organization it
-- imports is recorded here against the platform organization created (or
-- chosen) for it. The product then links its own organization row to the
-- platform organization on the first Decoda sign-in into it — only through
-- this reviewed row, never by name, domain or membership guesswork.
--
-- One legacy organization maps to exactly one platform organization per
-- product, and vice versa. Rows are written only by the reviewed import.
--
-- Rollback: drop the view and the table (nothing else references them).

CREATE TABLE platform.legacy_organization_links (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product                 TEXT NOT NULL CHECK (product IN ('rwa_guard', 'vault')),
    legacy_organization_id  TEXT NOT NULL CHECK (length(legacy_organization_id) BETWEEN 1 AND 128),
    organization_id         UUID NOT NULL REFERENCES platform.organizations(id),
    manifest_digest         TEXT NOT NULL CHECK (manifest_digest ~ '^[0-9a-f]{64}$'),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (product, legacy_organization_id),
    UNIQUE (product, organization_id)
);

-- Read by products (through the reader role) when a platform organization
-- with no product-side organization is entered for the first time.
CREATE VIEW platform_api.legacy_organization_links_v1 AS
SELECT
    l.product,
    l.legacy_organization_id,
    l.organization_id AS platform_organization_id,
    o.workos_organization_id
FROM platform.legacy_organization_links l
JOIN platform.organizations o ON o.id = l.organization_id
WHERE o.workos_organization_id IS NOT NULL;
