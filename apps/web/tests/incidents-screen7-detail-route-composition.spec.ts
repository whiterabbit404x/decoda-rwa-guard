/**
 * Screen 7 — incident detail-route composition.
 *
 * Regression coverage for the production bug where /incidents/[incidentId] rendered the
 * Digital Forensics Investigator hero AND THEN re-rendered the entire Incidents list +
 * Case File drawer beneath it (via `<IncidentsPanel initialSelectedId={incidentId} />`),
 * so the full-detail route looked like the list screen with a panel bolted on top — and
 * fetched the /investigation payload twice (investigator + drawer).
 *
 * The fix splits the two routes cleanly:
 *   • /incidents             → incidents list + selected Case File drawer (IncidentsPanel).
 *   • /incidents/[incidentId] → the STANDALONE forensic investigation experience, plus a
 *                               supplementary Case File tabs strip that reuses ONLY the
 *                               drawer's tab bodies (Timeline / Alerts / Evidence /
 *                               Response Actions) — never the list shell, never a second
 *                               /investigation fetch.
 *
 * Established repo style: source-level structural guards (no running server).
 */
import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

function appSource(...segments: string[]): string {
  return fs.readFileSync(path.join(__dirname, '..', 'app', ...segments), 'utf-8');
}

const LIST_PAGE = ['(product)', 'incidents', 'page.tsx'];
const DETAIL_PAGE = ['(product)', 'incidents', '[incidentId]', 'page.tsx'];
const TABS = ['incident-case-file-tabs.tsx'];
const PANEL = ['incidents-panel.tsx'];
const FORENSIC = ['forensic-investigator-panel.tsx'];

/* ── 1. /incidents keeps the list + Case File drawer, not the forensic hero ── */

test('1. /incidents renders the incidents list + Case File drawer (IncidentsPanel), not the investigator hero', () => {
  const list = appSource(...LIST_PAGE);
  // The list panel owns the table, KPIs, filters, and the selected Case File drawer.
  expect(list).toContain('<IncidentsPanel />');
  // The full Digital Forensics Investigator hero belongs to the detail route only.
  expect(list).not.toContain('ForensicInvestigatorPanel');
  expect(list).not.toContain('IncidentCaseFileTabs');
});

test('1b. the Case File panel still lives in the list panel, as a compact summary', () => {
  const panel = appSource(...PANEL);
  const tabs = appSource(...TABS);
  // The Case File aside remains on the list page and answers "what is this case and
  // what state is it in", with the full-investigation hand-off as its primary action.
  expect(panel).toContain('aria-label="Incident detail"');
  expect(panel).toContain('Case File');
  expect(panel).toContain('Integrity Summary');
  expect(panel).toContain('Open Full Investigation');
  // The tabbed forensic record moved to the full investigation, which has the width
  // for it — nothing was dropped, it was relocated.
  expect(tabs).toContain("label: 'Timeline'");
  expect(tabs).toContain("label: 'Evidence'");
  expect(tabs).toContain("label: 'Response Actions'");
});

/* ── 2. /incidents/[incidentId] is the full investigator ────────────────────── */

test('2. /incidents/[incidentId] renders the full Digital Forensics Investigator', () => {
  const detail = appSource(...DETAIL_PAGE);
  expect(detail).toContain('import ForensicInvestigatorPanel');
  expect(detail).toContain('<ForensicInvestigatorPanel incidentId={incidentId} />');
});

test('2b. the investigator hero still exposes header / AI summary / workflow / evidence / findings / report / re-run', () => {
  // The full-detail functionality that must be preserved lives in the forensic panel the
  // detail route renders — assert the panel still provides each required area/action.
  const forensic = appSource(...FORENSIC);
  expect(forensic).toContain('Incident header');            // incident header
  expect(forensic).toContain('AI Investigation Summary');   // AI Investigation Summary
  expect(forensic).toContain('Investigation Workflow');     // canonical workflow
  expect(forensic).toContain('Evidence — Corroborated');    // corroborated evidence
  expect(forensic).toContain('Digital Forensics Investigator'); // investigator findings
  expect(forensic).toContain('Generate Report');            // Generate Report
  expect(forensic).toContain('Re-run Investigation');       // Re-run Investigation
});

/* ── 3. the detail route drops the whole list-page composition ───────────────── */

test('3. the detail route does NOT render the incidents list panel', () => {
  const detail = appSource(...DETAIL_PAGE);
  // The literal Screen 7 bug: the list panel rendered beneath the investigator.
  expect(detail).not.toContain('IncidentsPanel');
  expect(detail).not.toContain('initialSelectedId={incidentId}');
});

test('3b. the detail route does NOT render list search / filters / table / Create Incident / KPIs / drawer', () => {
  const detail = appSource(...DETAIL_PAGE);
  const tabs = appSource(...TABS);
  const forbiddenOnDetail = [
    'Search incidents',          // incident search
    'aria-label="Severity filter"', // severity filter
    'aria-label="Status filter"',   // status filter
    'INCIDENT_TABLE_HEADERS',    // incident table
    'Create Incident',           // Create Incident
    'aria-label="Incident detail"', // the Case File drawer aside
    'Open Full Investigation',   // the drawer's primary link
    'Open Incidents',            // a list KPI tile
    'Critical Incidents',        // a list KPI tile
  ];
  for (const marker of forbiddenOnDetail) {
    expect(detail, `detail page must not contain "${marker}"`).not.toContain(marker);
    expect(tabs, `case-file tabs must not contain "${marker}"`).not.toContain(marker);
  }
  // No paginated list controls leak onto the detail experience either.
  expect(tabs).not.toContain('← Previous');
  expect(tabs).not.toContain('Next →');
});

/* ── 4. both navigation actions lead to the same detail route ────────────────── */

test('4. table "View Incident" and drawer "Open Full Investigation" both route to /incidents/{id}', () => {
  const panel = appSource(...PANEL);
  const canonical = 'href={`/incidents/${encodeURIComponent(incident.id)}`}';
  // Both controls target the identical canonical route (built from the incident's id).
  expect(panel.split(canonical).length - 1).toBeGreaterThanOrEqual(2);
  expect(panel).toContain('View Incident');
  expect(panel).toContain('Open Full Investigation');
});

/* ── 5. a Back to Incidents action returns to /incidents ─────────────────────── */

test('5. the detail header exposes a "← Back to Incidents" link to /incidents', () => {
  const detail = appSource(...DETAIL_PAGE);
  expect(detail).toContain('← Back to Incidents');
  expect(detail).toContain('href="/incidents"');
  // It is a real accessible <Link> (keyboard-activatable + browser back friendly).
  expect(detail).toMatch(/<Link[\s\S]*?href="\/incidents"[\s\S]*?← Back to Incidents\s*<\/Link>/);
});

/* ── 6. full-detail tabs/actions still work (Timeline/Evidence/Response Actions) ─ */

test('6. the detail route reuses the drawer tab bodies via IncidentCaseFileTabs (no list shell)', () => {
  const detail = appSource(...DETAIL_PAGE);
  const tabs = appSource(...TABS);
  // The detail route renders the supplementary Case File tabs …
  expect(detail).toContain('import IncidentCaseFileTabs');
  expect(detail).toContain('<IncidentCaseFileTabs incidentId={incidentId} />');
  // … which reuse the SAME tab bodies exported from the list panel (single source), so the
  // drawer and full page can never disagree — rather than re-mounting the whole panel.
  expect(tabs).toContain("from './incidents-panel'");
  expect(tabs).toContain('TimelineTab');
  expect(tabs).toContain('AlertsTab');
  expect(tabs).toContain('EvidenceTab');
  expect(tabs).toContain('ResponseActionsTab');
  // Timeline, Evidence, and Response Actions are reachable as tabs on the detail route.
  expect(tabs).toContain("key: 'timeline'");
  expect(tabs).toContain("label: 'Timeline'");
  expect(tabs).toContain("key: 'evidence'");
  expect(tabs).toContain("label: 'Evidence'");
  expect(tabs).toContain("key: 'response-actions'");
  expect(tabs).toContain("label: 'Response Actions'");
});

test('6b. the exported tab bodies exist in the list panel (single source of truth)', () => {
  const panel = appSource(...PANEL);
  expect(panel).toContain('export {');
  expect(panel).toContain('TimelineTab');
  expect(panel).toContain('AlertsTab');
  expect(panel).toContain('EvidenceTab');
  expect(panel).toContain('ResponseActionsTab');
  expect(panel).toContain('export type { TimelineEntry, AlertRow, EvidenceRow, ResponseActionRow }');
});

test('6c. the ?tab= deep link (e.g. forensic "View all evidence") selects the matching tab', () => {
  const tabs = appSource(...TABS);
  // The forensic panel links to /incidents/{id}?tab=evidence — the tabs honour ?tab=.
  expect(tabs).toContain('useSearchParams');
  expect(tabs).toContain("searchParams?.get('tab')");
  const forensic = appSource(...FORENSIC);
  expect(forensic).toContain('?tab=evidence');
});

test('6d. the Response Actions hand-off is preserved and stays approval-routed (Screen 8 boundary)', () => {
  const tabs = appSource(...TABS);
  // Recommend routes to the approval workflow — nothing is executed on the detail route.
  expect(tabs).toContain('/response-actions/recommend');
  expect(tabs).toContain('/response-actions?incident_id=');
  // No fund-moving / execution endpoints are invoked from the detail tabs.
  expect(tabs).not.toContain('/execute');
});

/* ── 7. no duplicate investigation request is introduced ─────────────────────── */

test('7. only the forensic hero fetches /investigation — the case-file tabs never do', () => {
  const detail = appSource(...DETAIL_PAGE);
  const tabs = appSource(...TABS);
  const forensic = appSource(...FORENSIC);
  // The forensic panel is the single owner of the /investigation fetch on this route.
  expect(forensic).toContain('/investigation');
  // Removing IncidentsPanel removes its per-selection /investigation fetch, and the
  // case-file tabs deliberately do NOT fetch the investigation payload at all.
  expect(detail).not.toContain('IncidentsPanel');
  expect(tabs).not.toContain('/investigation`');
  // The standalone AI triage panel mounts exactly once on this route — under its own
  // tab, so its /ai-triage lifecycle does not start on page load — and never from the
  // page itself, so there is still no double-mount.
  expect(detail).not.toContain('<AiInvestigationPanel');
  expect(tabs.split('<AiInvestigationPanel').length - 1).toBe(1);
  expect(tabs).toContain("activeTab === 'ai-investigation' && <AiInvestigationPanel");
});
