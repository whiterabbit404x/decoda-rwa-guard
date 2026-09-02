/**
 * Screen 7 – "Create Incident" control contract.
 *
 * The literal defect: the button carried a bare `disabled` JSX attribute, so it was dead
 * for every user in every workspace — not gated by RBAC, workspace state, a feature flag,
 * or a capability probe. It could never be enabled by any runtime condition.
 *
 * These tests pin the fixed behaviour: the control is wired to the canonical alert
 * escalation endpoint (the only backend path that creates an incident), it is disabled
 * ONLY when no escalatable alert exists, and the backend's RBAC refusal is surfaced
 * rather than pre-empted client-side.
 *
 * Source-level: reads .tsx files and asserts on string/structural presence. No browser.
 */
import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

function appSource(...segments: string[]): string {
  return fs.readFileSync(path.join(__dirname, '..', 'app', ...segments), 'utf-8');
}

const PANEL = 'incidents-panel.tsx';

/* ── 1. the button is no longer unconditionally disabled ─────────────────────── */

test('Create Incident is not hardcoded disabled', () => {
  const panel = appSource(PANEL);
  const button = panel.slice(
    panel.indexOf('data-testid="create-incident"'),
    panel.indexOf('Create Incident\'') > -1 ? undefined : panel.indexOf('</button>', panel.indexOf('data-testid="create-incident"')),
  );
  // The regression: a bare `disabled` attribute with no expression.
  expect(button).not.toMatch(/\sdisabled(\s|\/?>)/);
  expect(button).toContain('disabled={creatingIncident || !escalatableAlert}');
});

test('Create Incident renders with a stable test id and click handler', () => {
  const panel = appSource(PANEL);
  expect(panel).toContain('data-testid="create-incident"');
  expect(panel).toContain('onClick={() => void handleCreateIncident()}');
  expect(panel).toContain("{creatingIncident ? 'Creating…' : 'Create Incident'}");
});

/* ── 2. it runs the canonical escalation endpoint ────────────────────────────── */

test('Create Incident posts to the canonical alert escalation endpoint', () => {
  const panel = appSource(PANEL);
  expect(panel).toContain('/alerts/${encodeURIComponent(escalatableAlert.id)}/escalate');
  expect(panel).toContain("method: 'POST'");
  // No second creation path is invented: there is no standalone POST /incidents.
  expect(panel).not.toMatch(/fetch\(`\$\{apiUrl\}\/incidents`,\s*\{\s*\n?\s*method: 'POST'/);
});

test('Create Incident navigates to the persisted incident the backend returned', () => {
  const panel = appSource(PANEL);
  expect(panel).toContain('router.push(`/incidents/${encodeURIComponent(data.incident_id)}`)');
  // The incident id must come from the response, never optimistically invented client-side.
  expect(panel).toContain("setCreateIncidentError('The alert was escalated but no incident id was returned.')");
});

/* ── 3. the disabled state has a real, truthful cause ────────────────────────── */

test('the only disable conditions are in-flight creation and no escalatable alert', () => {
  const panel = appSource(PANEL);
  expect(panel).toContain('const [escalatableAlert, setEscalatableAlert] = useState<AlertRow | null>(null);');
  expect(panel).toContain('const [creatingIncident, setCreatingIncident] = useState(false);');
  expect(panel).toContain('setEscalatableAlert(firstEscalatableAlert(rows));');
});

test('the disabled tooltip states the real reason instead of a dead-end instruction', () => {
  const panel = appSource(PANEL);
  expect(panel).toContain('No alert is available to escalate.');
  // The old copy told the operator the control could never work here.
  expect(panel).not.toContain('Incident creation from alert requires alert escalation — use View Alert → Open Incident');
});

/* ── 4. escalation candidate selection ───────────────────────────────────────── */

test('an already-escalated or suppressed alert is never offered as a candidate', () => {
  const panel = appSource(PANEL);
  expect(panel).toContain('function firstEscalatableAlert(rows: AlertRow[]): AlertRow | null');
  expect(panel).toContain('if (row.incident_id || row.linked_incident_id) continue;');
  expect(panel).toContain("const NON_ESCALATABLE_ALERT_STATUSES = new Set(['suppressed']);");
});

test('the alerts probe scans a page of alerts, not a single row', () => {
  const panel = appSource(PANEL);
  // limit=1 could only answer "do alerts exist?" — never "is one escalatable?".
  expect(panel).not.toContain('/alerts?limit=1`');
  expect(panel).toContain('const ESCALATION_CANDIDATE_SCAN_LIMIT = 50;');
  expect(panel).toContain('/alerts?limit=${ESCALATION_CANDIDATE_SCAN_LIMIT}');
});

/* ── 5. RBAC is enforced server-side and surfaced, not simulated client-side ──── */

test('the backend permission refusal is surfaced verbatim', () => {
  const panel = appSource(PANEL);
  expect(panel).toContain('data-testid="create-incident-error"');
  expect(panel).toContain('role="alert"');
  expect(panel).toContain('if (!res.ok) {');
  // No client-side role gate: workspace role permissions are DB-overridable, so a
  // hardcoded role check would wrongly block a workspace that granted the permission.
  // The disable expression must carry the two workflow conditions and nothing else.
  expect(panel).toContain('disabled={creatingIncident || !escalatableAlert}');
  const handler = panel.slice(
    panel.indexOf('const handleCreateIncident'),
    panel.indexOf('const handleRecommend'),
  );
  expect(handler).not.toMatch(/role\s*===/);
  expect(handler).not.toContain('memberships');
});

test('the request carries the authenticated workspace headers', () => {
  const panel = appSource(PANEL);
  const handler = panel.slice(
    panel.indexOf('const handleCreateIncident'),
    panel.indexOf('const handleRecommend'),
  );
  expect(handler).toContain('...authHeaders()');
  expect(handler).toContain("'Content-Type': 'application/json'");
});

/* ── 6. the page is otherwise unchanged ──────────────────────────────────────── */

test('the incidents list composition is untouched', () => {
  const panel = appSource(PANEL);
  expect(panel).toContain('aria-label="Search incidents"');
  expect(panel).toContain('aria-label="Severity filter"');
  expect(panel).toContain('aria-label="Status filter"');
  expect(panel).toContain('aria-label="Assignee filter"');
  expect(panel).toContain('INCIDENT_TABLE_HEADERS');
  expect(panel).toContain('Open Incidents');
  expect(panel).toContain('Critical Incidents');
});

/* ── 7. behaviour: the real candidate selector, executed ─────────────────────── */

/* Resolve the on-disk Chromium (mirrors the Screen 7 nav spec) so the behavioural
 * tests run against the browser present in this environment. */
function resolveChromium(): string | undefined {
  const base = process.env.PLAYWRIGHT_BROWSERS_PATH || '/opt/pw-browsers';
  try {
    const dir = fs
      .readdirSync(base)
      .find((d) => d.startsWith('chromium-') && fs.existsSync(path.join(base, d, 'chrome-linux', 'chrome')));
    if (dir) return path.join(base, dir, 'chrome-linux', 'chrome');
  } catch {
    /* fall through to the Playwright default */
  }
  return undefined;
}
const chromiumExecutable = resolveChromium();
if (chromiumExecutable) test.use({ launchOptions: { executablePath: chromiumExecutable } });

/**
 * Lift the REAL `NON_ESCALATABLE_ALERT_STATUSES` + `firstEscalatableAlert` out of
 * incidents-panel.tsx and strip the type annotations, so the behavioural assertions
 * below execute the shipped logic rather than a copy that could silently drift.
 */
function escalationSelectorScript(): string {
  const panel = appSource(PANEL);
  const setStart = panel.indexOf('const NON_ESCALATABLE_ALERT_STATUSES');
  const fnStart = panel.indexOf('function firstEscalatableAlert');
  const fnEnd = panel.indexOf('\n}', fnStart) + 2;
  expect(setStart, 'NON_ESCALATABLE_ALERT_STATUSES must exist').toBeGreaterThan(-1);
  expect(fnStart, 'firstEscalatableAlert must exist').toBeGreaterThan(-1);
  const source = panel.slice(setStart, panel.indexOf('\n', setStart) + 1) + panel.slice(fnStart, fnEnd);
  return source
    .replace('function firstEscalatableAlert(rows: AlertRow[]): AlertRow | null', 'function firstEscalatableAlert(rows)')
    .concat('\nwindow.__pick = firstEscalatableAlert;');
}

async function pick(page: import('@playwright/test').Page, rows: unknown[]) {
  await page.setContent('<!doctype html><html><body></body></html>');
  await page.addScriptTag({ content: escalationSelectorScript() });
  return page.evaluate(
    (input) => (window as unknown as { __pick: (r: unknown[]) => { id?: string } | null }).__pick(input),
    rows,
  );
}

test('behaviour: the newest un-escalated alert is chosen', async ({ page }) => {
  const chosen = await pick(page, [
    { id: 'newest', status: 'open' },
    { id: 'older', status: 'open' },
  ]);
  expect(chosen?.id).toBe('newest');
});

test('behaviour: an alert already linked to an incident is skipped', async ({ page }) => {
  const chosen = await pick(page, [
    { id: 'already-escalated', status: 'open', incident_id: 'inc-1' },
    { id: 'escalatable', status: 'open' },
  ]);
  expect(chosen?.id).toBe('escalatable');
});

test('behaviour: linked_incident_id also marks an alert as already escalated', async ({ page }) => {
  const chosen = await pick(page, [
    { id: 'already-escalated', status: 'open', linked_incident_id: 'inc-1' },
    { id: 'escalatable', status: 'acknowledged' },
  ]);
  expect(chosen?.id).toBe('escalatable');
});

test('behaviour: a suppressed alert is never offered (the backend would 404 it)', async ({ page }) => {
  const chosen = await pick(page, [
    { id: 'suppressed-alert', status: 'SUPPRESSED' },
    { id: 'escalatable', status: 'open' },
  ]);
  expect(chosen?.id).toBe('escalatable');
});

test('behaviour: null when every alert is already escalated — Create Incident stays disabled', async ({ page }) => {
  const chosen = await pick(page, [
    { id: 'a', status: 'open', incident_id: 'inc-1' },
    { id: 'b', status: 'investigating', incident_id: 'inc-2' },
    { id: 'c', status: 'suppressed' },
  ]);
  expect(chosen).toBeNull();
});

test('behaviour: null for an empty alert list — no alert, no incident', async ({ page }) => {
  expect(await pick(page, [])).toBeNull();
});
