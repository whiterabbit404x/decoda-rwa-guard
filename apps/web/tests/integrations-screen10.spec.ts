import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { fmtLatency } from '../app/(product)/integration-gateway-types';

// Screen 10 was rebuilt into the Integration Gateway control plane. These source-grep
// tests pin the NEW contract: a real GET read model, a real Run Health Check mutation,
// deterministic recommendations, a derived connection risk, a persisted last-scan
// timestamp, and — critically — no secret material in the client.
const clientPath = path.join(__dirname, '..', 'app', '(product)', 'integrations-page-client.tsx');
const panelPath = path.join(__dirname, '..', 'app', '(product)', 'integration-gateway-agent-panel.tsx');
const typesPath = path.join(__dirname, '..', 'app', '(product)', 'integration-gateway-types.ts');
const src = fs.readFileSync(clientPath, 'utf-8');
const panel = fs.readFileSync(panelPath, 'utf-8');
const types = fs.readFileSync(typesPath, 'utf-8');

test('integrations route client exports a page component', () => {
  expect(src).toContain('export default function IntegrationsPageClient');
});

// Screen-10 cleanup: unavailable latency must render as "—", never a misleading "0 ms".
// The read model normalizes an unmeasured/backoff latency_ms=0 to null (see
// services/api/tests/test_integration_gateway.py); the UI must render that null as "—".
test('fmtLatency renders unavailable latency as an em-dash, never "0 ms"', () => {
  expect(fmtLatency(null)).toBe('—');
  expect(fmtLatency(undefined)).toBe('—');
});

test('fmtLatency renders a real measured latency in ms', () => {
  expect(fmtLatency(125)).toBe('125 ms');
});

test('integrations title and subtitle exist', () => {
  expect(src).toContain('>Integrations</h1>');
  expect(src).toContain('Operational control plane for every external dependency Decoda relies on');
});

test('Run Health Check and Add Provider actions exist', () => {
  expect(src).toContain('Run Health Check');
  expect(src).toContain('Add Provider');
});

test('summary metric tiles exist', () => {
  for (const label of ['Total Integrations', 'Healthy', 'Degraded', 'Disconnected', 'Recommendations']) {
    expect(src).toContain(label);
  }
});

test('tabs exist exactly (Providers / APIs / Webhooks / Connections)', () => {
  for (const label of ['Providers', 'APIs', 'Webhooks', 'Connections']) {
    expect(src).toContain(`label: '${label}'`);
  }
  // The old workspace-API-keys tab is gone; the tab is now external-service APIs.
  expect(src).not.toContain("label: 'API Keys'");
});

test('providers table columns exist exactly', () => {
  for (const header of ['Provider', 'Type', 'Status', 'Role', 'Last Check', 'Latency', 'Targets']) {
    expect(src).toContain(`'${header}'`);
  }
});

test('webhooks table columns exist exactly', () => {
  for (const header of ['Endpoint', 'Event Types', 'Status', 'Last Delivery', 'Success Rate', 'Failures', 'Retry']) {
    expect(src).toContain(`'${header}'`);
  }
});

test('connections table columns exist exactly', () => {
  for (const header of ['Source', 'Via', 'Destination', 'Purpose', 'State', 'Failover', 'Last Verified']) {
    expect(src).toContain(`'${header}'`);
  }
});

test('GET read model is read-only; Run Health Check is a POST mutation (spec §24)', () => {
  // The page load reads the control plane over the same-origin proxy…
  expect(src).toContain("fetch('/api/integrations/control-plane', { headers: authHeaders(), cache: 'no-store' })");
  // …and the ONLY mutating action helper posts.
  expect(src).toContain("method: 'POST'");
  expect(src).toContain("'/api/integrations/health-check'");
});

test('last scan renders the backend-persisted timestamp, never a fresh client clock', () => {
  // Uses the persisted last_scan.completed_at; there is no new Date() driving "last scan".
  expect(src).toContain('data.last_scan.completed_at');
  expect(src).not.toMatch(/last[_ ]?scan[\s\S]{0,40}new Date\(\)/i);
});

test('connection risk is derived from backend, never hard-coded LOW', () => {
  expect(panel).toContain('Connection Risk');
  expect(panel).toContain('risk?.level');
  // No literal hard-coded risk verdict in the panel.
  expect(panel).not.toMatch(/riskLevel\s*=\s*['"]low['"]/i);
});

test('does not expose raw secrets in the client', () => {
  expect(src).not.toContain('secret_token');
  expect(src).not.toContain('secret_hash');
  expect(src).not.toContain('revealedSecret');
  expect(src).not.toMatch(/\bpayload\.secret\b/);
  // The credential hint in the type model is always null (metadata only, no value).
  expect(types).toContain('credential_hint: string | null');
});

test('empty states exist for each tab', () => {
  expect(src).toContain('No providers are configured');
  expect(src).toContain('No webhooks configured');
  expect(src).toContain('No API integrations');
  expect(src).toContain('No connections');
});

test('management actions are RBAC-gated (button state is not the only guard)', () => {
  // Run Health Check is disabled without the manage permission; the backend re-checks it.
  expect(src).toContain('!canManage');
  expect(src).toContain("data?.permissions.can_manage");
});
