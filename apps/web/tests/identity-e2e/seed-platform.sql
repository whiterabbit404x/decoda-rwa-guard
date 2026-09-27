-- Platform rows as the website's provisioning + WorkOS webhooks would write them.
INSERT INTO platform.users (workos_user_id, email, email_verified, first_name, last_name) VALUES
  ('user_01MORGANE2E', 'morgan.diaz@harbor-trust.test', TRUE, 'Morgan', 'Diaz'),
  ('user_01RILEYE2E', 'riley.park@coastal-partners.test', TRUE, 'Riley', 'Park'),
  ('user_01PRIYAE2E', 'priya.shah@harbor-trust.test', TRUE, 'Priya', 'Shah');
INSERT INTO platform.organizations (workos_organization_id, name, slug) VALUES
  ('org_01HARBORE2E', 'Harbor Trust', 'harbor-trust'),
  ('org_01BEACONE2E', 'Beacon Capital', 'beacon-capital'),
  ('org_01COASTALE2E', 'Coastal Partners', 'coastal-partners');
INSERT INTO platform.organization_memberships (organization_id, user_id, role, status, source)
SELECT o.id, u.id, m.role, 'active', 'invitation'
FROM (VALUES ('org_01HARBORE2E', 'user_01MORGANE2E', 'admin'), ('org_01BEACONE2E', 'user_01MORGANE2E', 'admin'),
             ('org_01COASTALE2E', 'user_01MORGANE2E', 'member'), ('org_01COASTALE2E', 'user_01RILEYE2E', 'admin'),
             ('org_01HARBORE2E', 'user_01PRIYAE2E', 'member')) AS m(org, usr, role)
JOIN platform.organizations o ON o.workos_organization_id = m.org
JOIN platform.users u ON u.workos_user_id = m.usr;
INSERT INTO platform.organization_product_entitlements (organization_id, product, status)
SELECT o.id, e.product, e.status
FROM (VALUES ('org_01HARBORE2E', 'rwa_guard', 'pilot'), ('org_01BEACONE2E', 'rwa_guard', 'enabled'), ('org_01COASTALE2E', 'vault', 'enabled')) AS e(org, product, status)
JOIN platform.organizations o ON o.workos_organization_id = e.org;
