/**
 * External Watchlist — the founder console's independent monitoring of public
 * RWA blockchain infrastructure (/admin/external-watchlist).
 *
 * What these specs pin down:
 *
 *   1  NAVIGATION. The entry exists only in the founder console, only when the
 *      BACKEND reports the feature enabled, and never in the customer product
 *      navigation.
 *   2  FOUNDER ONLY. The pages render nothing but a refusal on a backend 403;
 *      the same-origin proxies add no authorization of their own (the backend
 *      is the single home of that rule) and forward only allowlisted params.
 *   3  TRUTHFUL EMPTINESS. No events / no findings / never polled read as
 *      exactly that — never as safe, healthy or "Live".
 *   4  ADD PROTOCOL. Defaults (30-day backfill, every profile), validation, and
 *      a payload that can never carry a key, signature or wallet connection.
 *   5  DETAIL, FINDINGS, BACKFILL, CONVERSION, PROSPECT REPORT, DISCLAIMERS.
 *
 * Run:
 *     cd apps/web && npx playwright test tests/external-watchlist.spec.ts
 */
import fs from 'node:fs';
import path from 'node:path';

import { expect, test } from '@playwright/test';

import {
  EXTERNAL_WATCHLIST_HREF,
  PILOT_MANAGEMENT_HREF,
  buildAdminConsoleNav,
  externalWatchlistEnabled,
  isActiveAdminNav,
} from '../app/admin/admin-console';
import {
  BACKFILL_OPTIONS,
  DEFAULT_BACKFILL_DAYS,
  DETAIL_NOTICE,
  DETECTION_PROFILE_FALLBACK,
  EVIDENCE_DISCLAIMER,
  EXTERNAL_SOURCE_LABEL,
  FINDING_CLASS_LABELS,
  FINDING_STATUS_OPTIONS,
  PUBLIC_INFRASTRUCTURE_BADGE,
  READ_ONLY_NOTE,
  WATCHLIST_NOTICE,
  WATCHLIST_STATUS_OPTIONS,
  apiErrorCode,
  apiErrorMessage,
  backfillProgressLabel,
  backfillStatusLabel,
  buildCreatePayload,
  buildProspectReportSvg,
  clampPercent,
  countLabel,
  emptyProtocolForm,
  formatRelativeTime,
  formatTimestamp,
  isEvmAddress,
  latestEventLabel,
  latestFindingLabel,
  networkLabels,
  prospectReportRows,
  prospectReportText,
  statusLabel,
  statusVariant,
  validateProtocolForm,
  verificationLabel,
  wrapText,
  type ProspectReport,
} from '../app/admin/external-watchlist/external-watchlist-view';

const WEB_ROOT = path.join(__dirname, '..');
const REPO_ROOT = path.join(WEB_ROOT, '..', '..');
const EWL_DIR = path.join(WEB_ROOT, 'app', 'admin', 'external-watchlist');
const PROXY_DIR = path.join(WEB_ROOT, 'app', 'api', 'admin', 'external-watchlists');

const read = (...parts: string[]) => fs.readFileSync(path.join(...parts), 'utf-8');

const listClient = read(EWL_DIR, 'external-watchlist-client.tsx');
const detailClient = read(EWL_DIR, '[id]', 'external-watchlist-detail-client.tsx');
const consoleGate = read(EWL_DIR, 'external-watchlist-console.tsx');
const addDialog = read(EWL_DIR, 'add-protocol-dialog.tsx');
const targetsTab = read(EWL_DIR, 'targets-tab.tsx');
const eventsTab = read(EWL_DIR, 'events-tab.tsx');
const findingsTab = read(EWL_DIR, 'findings-tab.tsx');
const evidenceTab = read(EWL_DIR, 'evidence-tab.tsx');
const convertDialog = read(EWL_DIR, 'convert-to-pilot-dialog.tsx');
const reportPanel = read(EWL_DIR, 'prospect-report-panel.tsx');
const apiModule = read(EWL_DIR, 'external-watchlist-api.ts');
const adminNav = read(WEB_ROOT, 'app', 'admin', 'admin-console-nav.tsx');
const adminLayout = read(WEB_ROOT, 'app', 'admin', 'layout.tsx');
const productNav = read(WEB_ROOT, 'app', 'product-nav.ts');
const styles = read(WEB_ROOT, 'app', 'styles.css');
const backendConfig = read(REPO_ROOT, 'services', 'api', 'app', 'domains', 'external_watchlist', 'config.py');

/** A parenthesised, implicitly concatenated Python string constant. */
function pythonStringConstant(source: string, name: string): string {
  const match = source.match(new RegExp(`^${name} = \\(\\n([\\s\\S]*?)\\n\\)`, 'm'));
  if (!match) throw new Error(`constant ${name} not found`);
  return [...match[1].matchAll(/'((?:[^'\\]|\\.)*)'/g)].map((part) => part[1]).join('');
}

function proxyFiles(dir: string): string[] {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(dir, entry.name);
    return entry.isDirectory() ? proxyFiles(full) : entry.name === 'route.ts' ? [full] : [];
  });
}

const SAMPLE_REPORT: ProspectReport = {
  heading: 'Administrative Change Detected',
  source_label: 'External Public Monitoring',
  protocol: 'Example RWA',
  protocol_website: 'https://example.org',
  network: 'Base Mainnet',
  contract: '0x1111111111111111111111111111111111111111',
  contract_explorer_url: 'https://basescan.org/address/0x1111111111111111111111111111111111111111',
  transaction: `0x${'ab'.repeat(32)}`,
  transaction_explorer_url: `https://basescan.org/tx/0x${'ab'.repeat(32)}`,
  block_number: 21_000_000,
  observed_time: '2026-09-01T12:00:00+00:00',
  observed_change: 'Privileged role granted: MINTER_ROLE',
  change_title: 'Privileged role granted: MINTER_ROLE',
  decoda_analysis: {
    observed_fact: 'RoleGranted was emitted by 0x1111…1111.',
    decoda_interpretation: 'A new account can now mint the token.',
    operational_authorization:
      'Public blockchain telemetry does not establish whether the change was operationally authorized by the organization.',
  },
  evidence_verification: {
    status: 'verified',
    evidence_sha256: 'c'.repeat(64),
    manifest_sha256: 'd'.repeat(64),
    signature_algorithm: 'HMAC-SHA256',
    public_key_signature: 'absent',
    production_secret: true,
  },
  disclaimer: EVIDENCE_DISCLAIMER,
  prepared_by: 'Decoda Security',
  generated_at: '2026-09-02T08:00:00+00:00',
};

// ── 1 · navigation ───────────────────────────────────────────────────────────
test.describe('founder console navigation', () => {
  test('the External Watchlist entry appears only when the backend enabled it', () => {
    expect(buildAdminConsoleNav({ externalWatchlistEnabled: false }).map((item) => item.label)).toEqual([
      'Dashboard',
      'Pilot Management',
      'System Health',
    ]);
    expect(buildAdminConsoleNav({ externalWatchlistEnabled: true })).toEqual([
      { href: '/dashboard', label: 'Dashboard' },
      { href: PILOT_MANAGEMENT_HREF, label: 'Pilot Management' },
      { href: EXTERNAL_WATCHLIST_HREF, label: 'External Watchlist' },
      { href: '/system-health', label: 'System Health' },
    ]);
    expect(EXTERNAL_WATCHLIST_HREF).toBe('/admin/external-watchlist');
    expect(PILOT_MANAGEMENT_HREF).toBe('/admin/customers');
  });

  test('only an explicit boolean true from the backend enables it', () => {
    expect(externalWatchlistEnabled({ features: { external_watchlist: { enabled: true } } })).toBe(true);
    for (const config of [
      null,
      undefined,
      {},
      { features: null },
      { features: {} },
      { features: { external_watchlist: null } },
      { features: { external_watchlist: { enabled: false } } },
      { features: { external_watchlist: { enabled: 'true' } } },
      { features: { external_watchlist: { enabled: 1 } } },
    ]) {
      expect(externalWatchlistEnabled(config as never)).toBe(false);
    }
  });

  test('the active entry follows the route, including detail pages', () => {
    expect(isActiveAdminNav('/admin/external-watchlist', EXTERNAL_WATCHLIST_HREF)).toBe(true);
    expect(isActiveAdminNav('/admin/external-watchlist/abc', EXTERNAL_WATCHLIST_HREF)).toBe(true);
    expect(isActiveAdminNav('/admin/external-watchlistx', EXTERNAL_WATCHLIST_HREF)).toBe(false);
    expect(isActiveAdminNav(null, EXTERNAL_WATCHLIST_HREF)).toBe(false);
  });

  test('the console bar renders only after the backend confirmed internal-admin access', () => {
    expect(adminNav).toContain("fetch('/api/admin/console-config'");
    expect(adminNav).toContain('showsInternalAdminLink(user)');
    expect(adminNav).toContain('if (!confirmed) {\n    return null;');
    expect(adminNav).toContain('externalWatchlistEnabled(config)');
    expect(adminLayout).toContain('<AdminConsoleNav />');
  });

  test('the customer product navigation never links to the watchlist', () => {
    expect(productNav).not.toContain('external-watchlist');
    expect(productNav).not.toContain('External Watchlist');
  });

  test('the console lives outside the (product) route group', () => {
    expect(fs.existsSync(path.join(WEB_ROOT, 'app', 'admin', 'external-watchlist', 'page.tsx'))).toBe(true);
    expect(fs.existsSync(path.join(WEB_ROOT, 'app', 'admin', 'external-watchlist', '[id]', 'page.tsx'))).toBe(true);
    expect(fs.existsSync(path.join(WEB_ROOT, 'app', '(product)', 'external-watchlist'))).toBe(false);
  });
});

// ── 2 · founder-only access ──────────────────────────────────────────────────
test.describe('founder-only route access', () => {
  test('a backend 403 renders a refusal and nothing else', () => {
    expect(consoleGate).toContain("if (status === 403) {\n          setState({ state: 'denied' });");
    expect(consoleGate).toContain('This area is restricted to Decoda internal staff.');
    expect(consoleGate).toContain('data-testid="external-watchlist-denied"');
    // A disabled flag is its own state, not an empty watchlist.
    expect(consoleGate).toContain("if (!feature || feature.enabled !== true) {\n          setState({ state: 'disabled' });");
    expect(consoleGate).toContain('External Watchlist is not enabled');
  });

  test('both pages render only through the gate', () => {
    expect(listClient).toContain('<ConsoleGate state={consoleState}>');
    expect(detailClient).toContain('<ConsoleGate state={consoleState}>');
  });

  test('every proxy forwards to the backend and performs no authorization of its own', () => {
    const files = proxyFiles(PROXY_DIR);
    expect(files.length).toBeGreaterThanOrEqual(14);
    for (const file of files) {
      const source = fs.readFileSync(file, 'utf-8');
      expect(source, file).toContain("import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';");
      expect(source, file).toMatch(/backendPath: `\/admin\/external-watchlists/);
      expect(source, file).not.toMatch(/is_internal_admin|internal_admin|role\s*===|isAdmin/);
      expect(source, file).toContain("export const dynamic = 'force-dynamic';");
    }
    const config = read(WEB_ROOT, 'app', 'api', 'admin', 'console-config', 'route.ts');
    expect(config).toContain('backendPath: `/admin/console-config`');
    expect(config).not.toMatch(/is_internal_admin|role\s*===/);
  });

  test('list proxies forward only allowlisted query parameters', () => {
    expect(read(PROXY_DIR, 'route.ts')).toContain("for (const key of ['q', 'network', 'status', 'limit', 'offset'])");
    expect(read(PROXY_DIR, '[watchlistId]', 'events', 'route.ts')).toContain(
      "for (const key of ['category', 'target_id', 'limit', 'offset'])",
    );
    expect(read(PROXY_DIR, '[watchlistId]', 'findings', 'route.ts')).toContain(
      "for (const key of ['status', 'severity', 'limit', 'offset'])",
    );
  });

  test('there is no proxy for any execution capability', () => {
    const routes = proxyFiles(PROXY_DIR).map((file) => path.relative(PROXY_DIR, file));
    for (const forbidden of ['execute', 'sign', 'response-actions', 'transactions', 'approvals', 'pause-contract', 'remediation']) {
      expect(routes.some((route) => route.split(path.sep).includes(forbidden)), forbidden).toBe(false);
    }
  });

  test('mutations go through the shared CSRF retry, never a bare POST', () => {
    expect(apiModule).toContain("import { mutateWithCsrfRetry, type AuthHeaders } from 'app/csrf-retry';");
    for (const source of [listClient, detailClient, targetsTab, findingsTab, convertDialog, evidenceTab]) {
      expect(source).not.toContain("method: 'POST'");
      expect(source).not.toMatch(/fetch\(/);
    }
  });
});

// ── 3 · truthful empty and unknown states ────────────────────────────────────
test.describe('empty and unknown states are never shown as healthy', () => {
  test('no events, no findings and no poll are named as such', () => {
    expect(latestEventLabel(null)).toBe('No events observed yet');
    expect(latestEventLabel({ event_name: '', observed_at: null })).toBe('No events observed yet');
    expect(latestFindingLabel(null)).toBe('No findings');
    expect(formatRelativeTime(null)).toBe('Never');
    expect(formatRelativeTime('not-a-date')).toBe('Never');
    expect(formatTimestamp(null)).toBe('—');
    expect(networkLabels([])).toBe('—');
  });

  test('a status the API did not send is Unavailable, never Live', () => {
    expect(statusLabel(undefined)).toBe('Unavailable');
    expect(statusLabel('healthy')).toBe('Unavailable');
    expect(statusVariant(undefined)).toBe('neutral');
    expect(statusVariant('live')).toBe('success');
    expect(statusVariant('degraded')).toBe('warning');
    expect(statusVariant('error')).toBe('danger');
    expect(WATCHLIST_STATUS_OPTIONS.map((option) => option.label)).toEqual(['Live', 'Backfilling', 'Paused', 'Degraded', 'Error']);
  });

  test('the empty directory invites adding a protocol and claims nothing', () => {
    expect(listClient).toContain('No public protocols are being monitored yet.');
    expect(listClient).toContain('Nothing is monitored until you do.');
    expect(listClient).toContain('data-testid="external-watchlist-empty"');
    expect(findingsTab).toContain('That is not a statement that the protocol is safe');
    expect(eventsTab).toContain('No data is not a clean bill of health');
    expect(detailClient).toContain('This is not a statement that nothing changed');
  });

  test('the overview only claims "polled recently" when the backend said live', () => {
    expect(detailClient).toContain("overview.status === 'live' ? 'All enabled targets polled recently' : undefined");
    expect(detailClient).toContain('Heartbeat, poll and telemetry are separate facts');
  });

  test('relative times and counts', () => {
    const now = new Date('2026-09-23T12:00:00Z');
    expect(formatRelativeTime('2026-09-23T11:59:50Z', now)).toBe('just now');
    expect(formatRelativeTime('2026-09-23T11:48:00Z', now)).toBe('12 minutes ago');
    expect(formatRelativeTime('2026-09-23T09:00:00Z', now)).toBe('3 hours ago');
    expect(formatRelativeTime('2026-09-18T12:00:00Z', now)).toBe('5 days ago');
    expect(latestEventLabel({ event_name: 'RoleGranted', observed_at: '2026-09-23T11:48:00Z' }, now)).toBe(
      'Latest: RoleGranted · 12 minutes ago',
    );
    expect(countLabel(1, 'contract', 'contracts')).toBe('1 contract');
    expect(countLabel(0, 'wallet', 'wallets')).toBe('0 wallets');
    expect(countLabel(undefined, 'wallet', 'wallets')).toBe('0 wallets');
  });
});

// ── 4 · add public protocol ──────────────────────────────────────────────────
test.describe('+ Monitor Public Protocol', () => {
  test('defaults: 30-day backfill, contract target, every detection profile on', () => {
    const form = emptyProtocolForm();
    expect(form.backfill_days).toBe(30);
    expect(DEFAULT_BACKFILL_DAYS).toBe(30);
    expect(form.target_type).toBe('contract');
    expect(form.detection_profiles).toEqual(DETECTION_PROFILE_FALLBACK.map((profile) => profile.key));
    expect(form.detection_profiles).toHaveLength(9);
    expect(BACKFILL_OPTIONS.map((option) => option.label)).toEqual(['None', '7 days', '14 days', '30 days']);
  });

  test('the fallback profiles match the backend vocabulary', () => {
    for (const profile of DETECTION_PROFILE_FALLBACK) {
      expect(backendConfig).toContain(`'${profile.key}'`);
    }
  });

  test('validation: name and network required, EVM address syntax, not the zero address', () => {
    const base = { ...emptyProtocolForm(), name: 'Example RWA', network: 'base-mainnet' };
    expect(validateProtocolForm(base)).toEqual({});
    expect(validateProtocolForm({ ...base, name: '  ' }).name).toBe('Protocol name is required.');
    expect(validateProtocolForm({ ...base, network: '' }).network).toBe('Choose a network.');
    expect(validateProtocolForm({ ...base, address: '0x123' }).address).toContain('40-hex-character');
    expect(validateProtocolForm({ ...base, address: `0x${'0'.repeat(40)}` }).address).toBe('The zero address cannot be monitored.');
    expect(validateProtocolForm({ ...base, website_url: 'https://a b' }).website_url).toBeDefined();
    expect(validateProtocolForm({ ...base, detection_profiles: [] }).detection_profiles).toBeDefined();
    expect(isEvmAddress('0x' + 'aB'.repeat(20))).toBe(true);
    expect(isEvmAddress('0x' + 'g'.repeat(40))).toBe(false);
  });

  test('the payload carries only public facts', () => {
    const payload = buildCreatePayload({
      ...emptyProtocolForm(),
      name: ' Example RWA ',
      network: 'base-mainnet',
      website_url: 'https://example.org',
      address: ' 0x' + 'Ab'.repeat(20) + ' ',
      label: 'Token',
      target_type: 'multisig',
      backfill_days: 14,
    });
    expect(payload).toEqual({
      name: 'Example RWA',
      network: 'base-mainnet',
      website_url: 'https://example.org',
      address: '0x' + 'Ab'.repeat(20),
      target_type: 'multisig',
      label: 'Token',
      backfill_days: 14,
      detection_profiles: DETECTION_PROFILE_FALLBACK.map((profile) => profile.key),
    });
    // No address → no target fields at all.
    expect(buildCreatePayload({ ...emptyProtocolForm(), name: 'X', network: 'base-mainnet' })).toEqual({
      name: 'X',
      network: 'base-mainnet',
      backfill_days: 30,
      detection_profiles: DETECTION_PROFILE_FALLBACK.map((profile) => profile.key),
    });
  });

  test('the form never asks for a key, signature or wallet connection', () => {
    expect(addDialog).toContain('Start Monitoring');
    expect(addDialog).toContain('{READ_ONLY_NOTE}');
    expect(READ_ONLY_NOTE).toContain('no wallet connection, private key, signature or write permission');
    const inputs = addDialog.match(/<(input|select|textarea)[^>]*>/g) ?? [];
    expect(inputs.length).toBeGreaterThan(0);
    for (const input of inputs) {
      expect(input).not.toMatch(/private|secret|mnemonic|seed|signature|password|walletconnect/i);
    }
    expect(addDialog).not.toMatch(/type="password"/);
  });

  test('networks without an RPC endpoint cannot be chosen', () => {
    expect(addDialog).toContain('disabled={!network.rpc_configured}');
    expect(addDialog).toContain('no RPC endpoint configured');
  });

  test('server errors are shown as the backend stated them', () => {
    const payload = { detail: { code: 'DUPLICATE_TARGET', message: 'This address is already monitored.' } };
    expect(apiErrorMessage(409, payload, 'The protocol could not be added')).toBe('This address is already monitored.');
    expect(apiErrorCode(payload)).toBe('DUPLICATE_TARGET');
    expect(apiErrorMessage(401, payload, 'x')).toContain('sign in again');
    expect(apiErrorMessage(500, {}, 'The protocol could not be added')).toBe('The protocol could not be added (HTTP 500).');
  });
});

// ── 5 · protocol detail ──────────────────────────────────────────────────────
test.describe('protocol details', () => {
  test('five tabs, the public-infrastructure badge and the prominent notice', () => {
    for (const label of ['Overview', 'Targets', 'Events', 'Findings', 'Evidence']) {
      expect(detailClient).toContain(`label: '${label}'`);
    }
    expect(PUBLIC_INFRASTRUCTURE_BADGE).toBe('External / Public Infrastructure');
    expect(detailClient).toContain('{PUBLIC_INFRASTRUCTURE_BADGE}');
    expect(detailClient).toContain('ewlNotice ewlNoticeProminent');
    expect(detailClient).toContain('detail.notices?.detail ?? DETAIL_NOTICE');
  });

  test('the overview shows the required monitoring facts', () => {
    for (const label of [
      'Monitoring Status',
      'Contracts monitored',
      'Wallets monitored',
      'Events — 24h',
      'Findings — 30d',
      'Last block processed',
      'RPC health',
      'Last successful poll',
    ]) {
      expect(detailClient).toContain(`label="${label}"`);
    }
    expect(detailClient).toContain('Recent activity');
  });

  test('target actions are read-only toward the chain', () => {
    for (const action of ['+ Add target', 'Edit', 'Pause', 'Remove', 'Decoded events', 'Explorer ↗', 'Diagnostic', 'Backfill 30d']) {
      expect(targetsTab).toContain(action);
    }
    expect(targetsTab).toContain('execution authority: none');
    for (const source of [targetsTab, detailClient, findingsTab]) {
      expect(source).not.toMatch(/Response action[^s]|Execute|Sign transaction|Pause contract|Remediate/);
    }
  });

  test('the events table labels how each row was read', () => {
    expect(eventsTab).toContain('INGEST_SOURCE_LABELS[event.ingest_source]');
    expect(eventsTab).toContain('estimated from block height');
  });
});

// ── 6 · findings ─────────────────────────────────────────────────────────────
test.describe('findings', () => {
  test('the table has the required columns', () => {
    const headers = [...findingsTab.matchAll(/<th>([^<]+)<\/th>/g)].map((match) => match[1]);
    expect(headers).toEqual(['Severity', 'Type', 'Target', 'Transaction', 'Block', 'Observed time', 'Initiating wallet', 'Status']);
  });

  test('the review statuses are exactly the six internal ones', () => {
    expect(FINDING_STATUS_OPTIONS.map((option) => option.label)).toEqual([
      'New',
      'Reviewed',
      'Expected',
      'Interesting',
      'Outreach Candidate',
      'Dismissed',
    ]);
  });

  test('the detail is labelled External Public Monitoring and splits the AI analysis in three', () => {
    expect(EXTERNAL_SOURCE_LABEL).toBe('External Public Monitoring');
    expect(findingsTab).toContain('{EXTERNAL_SOURCE_LABEL}');
    expect(findingsTab).toContain('1 · Observed fact');
    expect(findingsTab).toContain('2 · Decoda interpretation');
    expect(findingsTab).toContain('3 · Operational authorization');
    for (const section of ['Decoded parameters', 'Previous state', 'New state', 'Related telemetry', 'Evidence integrity']) {
      expect(findingsTab).toContain(section);
    }
  });

  test('outreach, evidence and prospect report are the only actions', () => {
    expect(findingsTab).toContain('Mark as Outreach Candidate');
    expect(findingsTab).toContain('Generate Evidence Package');
    expect(findingsTab).toContain('Generate Prospect Report');
    expect(findingsTab).toContain('There are no response');
  });

  test('finding language stays careful', () => {
    const labels = [...Object.values(FINDING_CLASS_LABELS), ...FINDING_STATUS_OPTIONS.map((option) => option.label)];
    for (const label of labels) {
      expect(label).not.toMatch(/attack|hack|exploit|compromis|malicious|breach|drain/i);
    }
  });
});

// ── 7 · disclaimers ──────────────────────────────────────────────────────────
test.describe('disclaimers', () => {
  test('the UI copy is the backend copy, word for word', () => {
    expect(WATCHLIST_NOTICE).toBe(pythonStringConstant(backendConfig, 'WATCHLIST_NOTICE'));
    expect(DETAIL_NOTICE).toBe(pythonStringConstant(backendConfig, 'DETAIL_NOTICE'));
    expect(EVIDENCE_DISCLAIMER).toBe(pythonStringConstant(backendConfig, 'EVIDENCE_DISCLAIMER'));
    expect(WATCHLIST_NOTICE).toBe(
      'External Watchlist uses publicly available blockchain data. Monitored organizations have not necessarily authorized, endorsed, or partnered with Decoda Security.',
    );
  });

  test('every surface renders its notice', () => {
    expect(listClient).toContain("feature.notices?.watchlist ?? WATCHLIST_NOTICE");
    expect(listClient).toContain('Independent monitoring of publicly available RWA blockchain infrastructure.');
    expect(evidenceTab).toContain('{EVIDENCE_DISCLAIMER}');
    expect(reportPanel).toContain('{report.disclaimer}');
  });
});

// ── 8 · backfill state ───────────────────────────────────────────────────────
test.describe('historical backfill state', () => {
  test('progress reads "N / M days analyzed" and never overstates', () => {
    expect(backfillProgressLabel({ status: 'running', percent: 71.3, days_analyzed: 21.4, requested_days: 30, planned: true })).toBe(
      '21 / 30 days analyzed',
    );
    expect(backfillProgressLabel({ status: 'completed', percent: 100, days_analyzed: 30, requested_days: 30, planned: true })).toBe(
      '30 / 30 days analyzed',
    );
    expect(backfillProgressLabel({ status: 'running', percent: 100, days_analyzed: 45, requested_days: 30, planned: true })).toBe(
      '30 / 30 days analyzed',
    );
    expect(backfillProgressLabel({ status: 'pending', percent: 0, days_analyzed: 0, requested_days: 14, planned: false })).toBe(
      'Queued · 14 days requested',
    );
    expect(backfillProgressLabel(null)).toBeNull();
  });

  test('status labels and percent clamping', () => {
    expect(['pending', 'running', 'completed', 'partial', 'failed'].map(backfillStatusLabel)).toEqual([
      'Pending',
      'Running',
      'Completed',
      'Partial',
      'Failed',
    ]);
    expect(backfillStatusLabel(undefined)).toBe('Not requested');
    expect(clampPercent(140)).toBe(100);
    expect(clampPercent(-3)).toBe(0);
    expect(clampPercent(Number.NaN)).toBe(0);
  });

  test('the overview and the directory show the progress bar', () => {
    expect(detailClient).toContain('aria-label="Historical backfill progress"');
    expect(listClient).toContain("row.status === 'backfilling' && row.backfill");
  });
});

// ── 9 · conversion ───────────────────────────────────────────────────────────
test.describe('Convert to Pilot Workspace', () => {
  test('an explicit, acknowledged founder action', () => {
    expect(convertDialog).toContain('confirm: true');
    expect(convertDialog).toContain('disabled={!csrfReady || busy || !acknowledged}');
    expect(convertDialog).toContain('does not notify or invite the organization');
    expect(convertDialog).toContain('<strong>Does not invite anyone.</strong>');
    expect(convertDialog).toContain('<strong>inactive drafts</strong>');
    expect(detailClient).toContain('Convert to Pilot Workspace');
  });

  test('a converted protocol never claims customer authorization', () => {
    expect(detailClient).toContain('Customer authorization is not recorded here.');
    expect(detailClient).toContain('{detail.conversion ? null : (');
  });
});

// ── 10 · prospect report ─────────────────────────────────────────────────────
test.describe('prospect report', () => {
  test('rows are the shareable fields only', () => {
    expect(prospectReportRows(SAMPLE_REPORT).map((row) => row.label)).toEqual([
      'Protocol',
      'Network',
      'Contract',
      'Transaction',
      'Block',
      'Observed time',
      'Observed change',
      'Evidence verification',
      'Evidence SHA-256',
    ]);
  });

  test('the exported image and text never carry internal fields', () => {
    const withInternals = {
      ...SAMPLE_REPORT,
      id: 'finding-uuid-123',
      watchlist_id: 'watchlist-uuid-456',
      severity: 'high',
      confidence: 0.93,
      status: 'outreach_candidate',
      rpc_url: 'https://secret-rpc.example/key-abc',
      score_inputs: { internal: true },
    } as ProspectReport;
    const { svg, width, height } = buildProspectReportSvg(withInternals);
    const text = prospectReportText(withInternals);
    for (const output of [svg, text]) {
      for (const internal of ['finding-uuid-123', 'watchlist-uuid-456', 'outreach_candidate', 'secret-rpc', 'key-abc', '0.93']) {
        expect(output).not.toContain(internal);
      }
      expect(output).toContain('Administrative Change Detected');
      expect(output).toContain('Example RWA');
    }
    expect(text).toContain(EVIDENCE_DISCLAIMER);
    expect(svg).toContain('does not imply a commercial');
    expect(svg.startsWith('<svg xmlns="http://www.w3.org/2000/svg"')).toBe(true);
    expect(width).toBe(960);
    expect(height).toBeGreaterThan(400);
  });

  test('the three analysis statements are all present', () => {
    const text = prospectReportText(SAMPLE_REPORT);
    expect(text).toContain('Observed fact: RoleGranted');
    expect(text).toContain('Decoda interpretation: A new account');
    expect(text).toContain('Operational authorization: Public blockchain telemetry does not establish');
  });

  test('markup in report fields is escaped in the image', () => {
    const { svg } = buildProspectReportSvg({ ...SAMPLE_REPORT, protocol: '<script>alert(1)</script> & Co' });
    expect(svg).not.toContain('<script>');
    expect(svg).toContain('&lt;script&gt;');
    expect(svg).toContain('&amp; Co');
  });

  test('verification labels distinguish development keys and signatures', () => {
    expect(verificationLabel(SAMPLE_REPORT.evidence_verification)).toBe('Verified (HMAC-SHA256 seal)');
    expect(verificationLabel({ ...SAMPLE_REPORT.evidence_verification, public_key_signature: 'valid' })).toBe(
      'Verified (HMAC-SHA256 seal and Ed25519 signature)',
    );
    expect(verificationLabel({ ...SAMPLE_REPORT.evidence_verification, production_secret: false })).toContain('development signing key');
    expect(verificationLabel({ ...SAMPLE_REPORT.evidence_verification, status: 'verification_failed' })).toBe('Verification failed');
    expect(verificationLabel(null)).toBe('Not verified');
  });

  test('long hashes wrap instead of overflowing the image', () => {
    const lines = wrapText(`Evidence ${'f'.repeat(150)}`, 72);
    expect(lines.every((line) => line.length <= 72)).toBe(true);
    expect(lines.join('')).toContain('f'.repeat(72));
    expect(wrapText('', 10)).toEqual(['—']);
  });

  test('Export Screenshot draws from the report object, not the page', () => {
    expect(reportPanel).toContain('buildProspectReportSvg(report)');
    expect(reportPanel).toContain('Export Screenshot');
    expect(reportPanel).not.toMatch(/html2canvas|document\.body\.innerHTML|outerHTML/);
  });
});

// ── 11 · styling ─────────────────────────────────────────────────────────────
test.describe('styling', () => {
  test('the console classes are defined and use the theme tokens', () => {
    for (const selector of ['.adminShell', '.adminConsoleBar', '.adminConsoleNav a.active', '.ewlNotice', '.ewlNoticeProminent', '.ewlBadge', '.ewlProgressFill', '.ewlReport', '.ewlSourceLabel']) {
      expect(styles).toContain(`${selector} {`);
    }
    // Theme tokens only: a hardcoded colour would ignore the light/dark themes.
    const block = styles.slice(styles.indexOf('.adminShell {'), styles.indexOf('.ewlReportDisclaimer {'));
    expect(block).not.toMatch(/#[0-9a-f]{3,8}\b|rgba?\(/i);
  });
});
