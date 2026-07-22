-- Shared parent rows for the Datto workspace, committed once before the scenarios run.
-- Only the columns that are NOT NULL without a default (derived from the real migrations
-- 0001-0128) are supplied; everything else takes its migration default.
INSERT INTO users (id, email, password_hash, full_name)
VALUES ('11111111-1111-1111-1111-111111111111', 'ops@decoda.test', 'x', 'Ops User')
ON CONFLICT (id) DO NOTHING;

INSERT INTO workspaces (id, name, slug, created_by_user_id)
VALUES ('4fffd3f9-d55f-456f-8a7e-8b9ed2083721', 'Datto WS', 'datto-ws',
        '11111111-1111-1111-1111-111111111111')
ON CONFLICT (id) DO NOTHING;

INSERT INTO assets (id, workspace_id, name, asset_type, chain_network, identifier,
                    created_by_user_id, updated_by_user_id)
VALUES ('22222222-2222-2222-2222-222222222222',
        '4fffd3f9-d55f-456f-8a7e-8b9ed2083721', 'Datto USDC', 'tokenized_rwa',
        'base-mainnet', '0x833589fcd6edb6e08f4c7c32d4f71b54bda02913',
        '11111111-1111-1111-1111-111111111111',
        '11111111-1111-1111-1111-111111111111')
ON CONFLICT (id) DO NOTHING;
