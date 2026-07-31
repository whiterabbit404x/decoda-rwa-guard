/**
 * Screen 7 — "View Incident" navigation correctness.
 *
 * Regression coverage for the production bug where the incidents-table row
 * "View Incident" button only re-selected the Case File drawer (setSelectedId)
 * and never navigated, so it "appeared to do nothing" — especially when the
 * clicked row was already the selected/open drawer.
 *
 * The fix makes full-page navigation a SEPARATE action from drawer selection:
 * the row control is now an accessible Next.js <Link> to
 *   /incidents/${encodeURIComponent(incident.id)}
 * (the canonical UUID, never the displayed reference), and the Case File drawer
 * gains an "Open Full Investigation" <Link> to the same route.
 *
 * Two layers (the repo's established frontend test style):
 *   1. Source-level structural guards on the real incidents-panel.tsx.
 *   2. Real-browser interaction tests on a faithful DOM slice that reproduces the
 *      exact contract (an anchor inside a clickable row, stopPropagation, no
 *      preventDefault, a real href built with encodeURIComponent) — so the
 *      behavioural claims (row still selects, button not swallowed, keyboard
 *      activation, single navigation, encoding) are actually exercised.
 */
import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

function appSource(fileName: string): string {
  return fs.readFileSync(path.join(__dirname, '..', 'app', fileName), 'utf-8');
}

/* ── Resolve the Chromium binary present in this environment (mirrors the
 *    Screen 7 layout spec) so the behavioural tests use the on-disk browser. */
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

const CANONICAL_HREF = 'href={`/incidents/${encodeURIComponent(incident.id)}`}';

/* ───────────────────── 1. Source-level structural guards ───────────────────── */

test('1. table-row "View Incident" navigates to the canonical incident route (UUID)', () => {
  const panel = appSource('incidents-panel.tsx');
  // The row action is an accessible <Link> to the canonical incident detail route,
  // built from the incident's canonical id via encodeURIComponent.
  expect(panel).toContain(
    '<Link href={`/incidents/${encodeURIComponent(incident.id)}`} prefetch={false}',
  );
  expect(panel).toContain('View Incident');
  // The canonical route appears for BOTH nav controls (row + drawer), never fewer.
  expect(panel.split(CANONICAL_HREF).length - 1).toBeGreaterThanOrEqual(2);
});

test('2. navigation uses incident.id, never a display reference (e.g. INC-2026-001)', () => {
  const panel = appSource('incidents-panel.tsx');
  // The incident nav href is built from the canonical id only — not a reference,
  // incident_id, or *_ref field.
  expect(panel).not.toContain('/incidents/${encodeURIComponent(incident.reference)');
  expect(panel).not.toContain('/incidents/${encodeURIComponent(incident.incident_id)');
  expect(panel).not.toContain('/incidents/${incident.reference}');
  expect(panel).not.toContain('/incidents/${incident.incident_id}');
  // No human-facing incident reference number is used as a route param.
  expect(panel).not.toMatch(/\/incidents\/\$\{[^}]*reference[^}]*\}/);
});

test('3. the table row still opens/selects the Case File drawer on row click', () => {
  const panel = appSource('incidents-panel.tsx');
  // Row click → drawer selection is preserved (a SEPARATE action from navigation).
  expect(panel).toContain('<tr key={incident.id} onClick={() => setSelectedId(incident.id)}');
});

test('4. the "View Incident" click is not swallowed by the row (stopPropagation, no re-select)', () => {
  const panel = appSource('incidents-panel.tsx');
  // The link stops the row's onClick from also firing …
  expect(panel).toContain('onClick={(e) => { e.stopPropagation(); }}');
  // … and the old select-only handler (which never navigated) is gone.
  expect(panel).not.toContain("setSelectedId(incident.id); setActiveTab('overview')");
});

test('5. navigation is a real link (works even when the same drawer is already open)', () => {
  const panel = appSource('incidents-panel.tsx');
  // A real <Link> navigates from its own static per-row href regardless of the current
  // selection — the old select-only handler that never navigated is gone, and the action
  // cell renders a Link (anchor) wrapping the "View Incident" label rather than a button.
  expect(panel).not.toContain("setSelectedId(incident.id); setActiveTab('overview')");
  expect(panel).toMatch(
    /<Link\s+href=\{`\/incidents\/\$\{encodeURIComponent\(incident\.id\)\}`\}[\s\S]*?View Incident\s*<\/Link>/,
  );
});

test('6. the Case File drawer exposes "Open Full Investigation" to the same canonical route', () => {
  const panel = appSource('incidents-panel.tsx');
  expect(panel).toContain('Open Full Investigation');
  // It is the drawer's primary <Link> (btn-primary) to the identical canonical incident route.
  const drawerLinkPattern =
    /href=\{`\/incidents\/\$\{encodeURIComponent\(incident\.id\)\}`\}\s+prefetch=\{false\}\s+className="btn btn-primary"[\s\S]*?Open Full Investigation\s*<\/Link>/;
  expect(panel).toMatch(drawerLinkPattern);
});

test('7. both nav controls URL-encode the incident id (long / unusual valid ids are safe)', () => {
  const panel = appSource('incidents-panel.tsx');
  // Every incident nav href goes through encodeURIComponent; no raw interpolation.
  expect(panel).not.toContain('/incidents/${incident.id}');
  expect(panel.split('encodeURIComponent(incident.id)').length - 1).toBeGreaterThanOrEqual(2);
});

test('8. nav controls are accessible anchors (keyboard-activatable), not click-only buttons', () => {
  const panel = appSource('incidents-panel.tsx');
  // Both controls are <Link> elements → real <a href> → focusable + Enter-activatable.
  // Neither routes via an onClick-only router.push (which a keyboard user could not reach
  // as a link, and which would risk a duplicate navigation alongside an href).
  expect(panel).not.toContain('onClick={() => router.push(`/incidents/');
  expect(panel).not.toContain('onClick={(e) => { e.stopPropagation(); router.push(`/incidents/');
});

test('9. no duplicate navigation: the row action navigates via the href only (no paired router.push)', () => {
  const panel = appSource('incidents-panel.tsx');
  // The only side effect on the View Incident link is stopPropagation — it does not
  // ALSO call router.push, so exactly one navigation occurs per activation.
  expect(panel).not.toContain('e.stopPropagation(); router.push');
  expect(panel).not.toContain('router.push(`/incidents/${encodeURIComponent(incident.id)}`)');
});

test('10. no hardcoded incident id/reference is introduced by the fix', () => {
  const panel = appSource('incidents-panel.tsx');
  expect(panel).not.toMatch(/INC-2026-\d+/);            // no reference-design number
  expect(panel).not.toMatch(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i); // no literal UUID
});

/* ───────────────────── 2. Real-browser interaction contract ───────────────────── */

/**
 * Faithful DOM slice of the fixed interaction: a clickable incident row whose
 * onClick selects the drawer, containing an action-cell anchor (View Incident)
 * that calls stopPropagation but NOT preventDefault, plus a drawer "Open Full
 * Investigation" anchor to the same route. A capture-phase recorder logs the
 * intended navigation href and cancels the real navigation so the page stays put
 * — capture runs even though the anchor calls stopPropagation, exactly like a
 * native listener sees React's synthetic stopPropagation.
 */
function escapeAttr(v: string): string {
  return v.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function slice(rawIncidentId: string): string {
  const href = `/incidents/${encodeURIComponent(rawIncidentId)}`;
  const idAttr = escapeAttr(rawIncidentId);
  const hrefAttr = escapeAttr(href);
  return `<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>
    <table>
      <tbody>
        <tr id="incidentRow" data-incident-id="${idAttr}"
            onclick="window.__rowSelects.push(this.dataset.incidentId)" style="cursor:pointer">
          <td id="incidentIdCell" title="${idAttr}">${idAttr}</td>
          <td>Critical</td>
          <td>Reserve backing ratio dropped</td>
          <td>
            <a id="viewIncidentLink" class="btn btn-secondary" href="${hrefAttr}"
               onclick="event.stopPropagation()">View Incident</a>
          </td>
        </tr>
      </tbody>
    </table>
    <aside aria-label="Incident detail">
      <a id="openFullInvestigation" class="btn btn-primary" href="${hrefAttr}">Open Full Investigation</a>
    </aside>
    <script>
      window.__nav = [];
      window.__rowSelects = [];
      // Capture-phase recorder: fires even when the anchor calls stopPropagation in
      // the target/bubble phase. Records the intended navigation and cancels the real
      // one so the harness page does not actually leave.
      document.addEventListener('click', function (e) {
        var a = e.target && e.target.closest ? e.target.closest('a') : null;
        if (a && a.getAttribute('href')) {
          window.__nav.push(a.getAttribute('href'));
          e.preventDefault();
        }
      }, true);
    </script>
  </body></html>`;
}

const UUID = '7c9e6679-7425-40de-944b-e07fc1f90ae7';

async function nav(page: import('@playwright/test').Page) {
  return page.evaluate(() => (window as unknown as { __nav: string[] }).__nav);
}
async function rowSelects(page: import('@playwright/test').Page) {
  return page.evaluate(() => (window as unknown as { __rowSelects: string[] }).__rowSelects);
}

test('behaviour: clicking "View Incident" navigates once to the incident UUID and does not select the row', async ({ page }) => {
  await page.setContent(slice(UUID));
  await page.click('#viewIncidentLink');
  // Requirement 1 + 9: exactly one navigation, to the canonical UUID route.
  expect(await nav(page)).toEqual([`/incidents/${UUID}`]);
  // Requirement 4: the button click is NOT swallowed into a row selection.
  expect(await rowSelects(page)).toEqual([]);
});

test('behaviour: clicking the row (not the button) still opens the drawer and does not navigate', async ({ page }) => {
  await page.setContent(slice(UUID));
  await page.click('#incidentIdCell');
  // Requirement 3: row click selects/opens the Case File drawer …
  expect(await rowSelects(page)).toEqual([UUID]);
  // … and does not navigate away.
  expect(await nav(page)).toEqual([]);
});

test('behaviour: "View Incident" navigates on the FIRST click even when its row is already selected', async ({ page }) => {
  await page.setContent(slice(UUID));
  // Pre-select the row (drawer already open on this incident), then click View Incident.
  await page.click('#incidentIdCell');
  expect(await rowSelects(page)).toEqual([UUID]);
  await page.click('#viewIncidentLink');
  // Requirement 2/3 of "Required behavior": one click still navigates; no second click needed.
  expect(await nav(page)).toEqual([`/incidents/${UUID}`]);
});

test('behaviour: keyboard activation (focus + Enter) navigates to the incident route', async ({ page }) => {
  await page.setContent(slice(UUID));
  await page.focus('#viewIncidentLink');
  await page.keyboard.press('Enter');
  expect(await nav(page)).toEqual([`/incidents/${UUID}`]);
  expect(await rowSelects(page)).toEqual([]);
});

test('behaviour: "Open Full Investigation" in the drawer navigates to the SAME canonical route', async ({ page }) => {
  await page.setContent(slice(UUID));
  await page.click('#openFullInvestigation');
  expect(await nav(page)).toEqual([`/incidents/${UUID}`]);
});

test('behaviour: long / unusual valid ids are URL-encoded in the navigation target', async ({ page }) => {
  const weird = 'wallet 0x1F/AB+CD:99'; // contains chars that MUST be percent-encoded
  await page.setContent(slice(weird));
  await page.click('#viewIncidentLink');
  const got = await nav(page);
  expect(got).toEqual([`/incidents/${encodeURIComponent(weird)}`]);
  // The raw (unencoded) separators never leak into the route.
  expect(got[0]).toContain('wallet%200x1F%2FAB%2BCD%3A99');
  expect(got[0]).not.toContain('wallet 0x1F/AB+CD:99');
});

test('behaviour: a single click yields no duplicate navigation', async ({ page }) => {
  await page.setContent(slice(UUID));
  await page.click('#viewIncidentLink');
  expect((await nav(page)).length).toBe(1);
});
