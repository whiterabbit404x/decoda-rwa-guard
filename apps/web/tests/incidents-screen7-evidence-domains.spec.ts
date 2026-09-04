/**
 * Screen 7 — forensic evidence directory + lifecycle timeline contract tests.
 *
 * Two layers (the repo's established frontend test style — no running server):
 *   1. Executable unit tests for the pure presentation helpers.
 *   2. Source-level structural tests asserting the Evidence tab and the forensic
 *      timeline render the required states, read the real backend payload, and
 *      hardcode none of the reference-design values.
 */
import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

import {
  EVIDENCE_DOMAINS,
  actorLabel,
  artifactEvidenceSource,
  artifactTypeLabel,
  isSimulatedArtifact,
  domainAccentVar,
  domainBorderVar,
  domainCount,
  domainLabel,
  domainSurfaceVar,
  emptyEvidenceMessage,
  enforcementEvaluations,
  evidencePackageAbsenceLabel,
  filterArtifacts,
  forensicDay,
  formatForensicTime,
  hasEvidencePackage,
  integrityLabel,
  integrityVariant,
  linkScopeCaveat,
  loadStateFor,
  policyDecisionVariant,
  shortDigest,
  showsImmutableMark,
  snapshotStatusLabel,
  snapshotStatusVariant,
  sortTimelineEvents,
  timelineDayHeadings,
  type IncidentEvidenceArtifact,
  type IncidentTimelineEvent,
} from '../app/incident-forensics-presentation';

function appSource(fileName: string): string {
  return fs.readFileSync(path.join(__dirname, '..', 'app', fileName), 'utf-8');
}

function styles(): string {
  return fs.readFileSync(path.join(__dirname, '..', 'app', 'styles.css'), 'utf-8');
}

/* ───────────────── 1. Four evidence domains ───────────────────────────── */

test('exactly four forensic evidence domains, in canonical order', () => {
  expect([...EVIDENCE_DOMAINS]).toEqual(['ON_CHAIN', 'OPERATIONAL', 'POLICY', 'HUMAN_ACTION']);
});

test('each domain is labelled distinctly', () => {
  const labels = EVIDENCE_DOMAINS.map(domainLabel);
  expect(labels).toEqual(['On-Chain', 'Operational', 'Policy', 'Human Actions']);
  expect(new Set(labels).size).toBe(4);
});

test('each domain uses a DISTINCT existing theme token (never a hard-coded colour)', () => {
  const accents = EVIDENCE_DOMAINS.map(domainAccentVar);
  expect(new Set(accents).size).toBe(4);
  // Every accent/surface/border resolves through a CSS custom property.
  for (const domain of EVIDENCE_DOMAINS) {
    expect(domainAccentVar(domain)).toMatch(/^var\(--/);
    expect(domainSurfaceVar(domain)).toMatch(/^var\(--/);
    expect(domainBorderVar(domain)).toMatch(/^var\(--/);
  }
});

test('every domain token the presentation layer names is defined in the theme', () => {
  const css = styles();
  const tokens = new Set<string>();
  for (const domain of EVIDENCE_DOMAINS) {
    for (const value of [domainAccentVar(domain), domainSurfaceVar(domain), domainBorderVar(domain)]) {
      const match = /^var\((--[a-z0-9-]+)\)$/.exec(value);
      if (match) tokens.add(match[1]);
    }
  }
  expect(tokens.size).toBeGreaterThan(0);
  for (const token of tokens) {
    expect(css).toContain(`${token}:`);
  }
});

/* ───────────────── 2. Counts come from the API ────────────────────────── */

test('domain counts are read from the backend payload', () => {
  const counts = { on_chain: 14, operational: 6, policy: 4, human_actions: 9, total: 33 };
  expect(domainCount(counts, 'ON_CHAIN')).toBe(14);
  expect(domainCount(counts, 'OPERATIONAL')).toBe(6);
  expect(domainCount(counts, 'POLICY')).toBe(4);
  expect(domainCount(counts, 'HUMAN_ACTION')).toBe(9);
});

test('a count the backend did not report is null — never a rendered zero', () => {
  expect(domainCount({ on_chain: 2 }, 'POLICY')).toBeNull();
  expect(domainCount(null, 'ON_CHAIN')).toBeNull();
  expect(domainCount(undefined, 'ON_CHAIN')).toBeNull();
  // A real zero stays a zero: "none collected" and "not reported" stay distinct.
  expect(domainCount({ policy: 0 }, 'POLICY')).toBe(0);
});

test('the card renders "not reported" for an absent bucket, never a fabricated 0', () => {
  const src = appSource('incident-evidence-tab.tsx');
  expect(src).toContain("count === null ? '—' : count");
  expect(src).toContain('not reported');
});

/* ───────────────── 3. Domain filtering ────────────────────────────────── */

const ARTIFACTS: IncidentEvidenceArtifact[] = [
  { id: '1', domain: 'ON_CHAIN', artifact_type: 'telemetry_event' },
  { id: '2', domain: 'ON_CHAIN', artifact_type: 'transaction_receipt' },
  { id: '3', domain: 'OPERATIONAL', artifact_type: 'reconciliation_output' },
  { id: '4', domain: 'POLICY', artifact_type: 'policy_decision' },
  { id: '5', domain: 'HUMAN_ACTION', artifact_type: 'approval_decision' },
  { id: '6', domain: null, artifact_type: 'something_new' },
];

test('selecting a domain filters the directory to that domain only', () => {
  expect(filterArtifacts(ARTIFACTS, 'ON_CHAIN').map((a) => a.id)).toEqual(['1', '2']);
  expect(filterArtifacts(ARTIFACTS, 'POLICY').map((a) => a.id)).toEqual(['4']);
  expect(filterArtifacts(ARTIFACTS, 'HUMAN_ACTION').map((a) => a.id)).toEqual(['5']);
});

test('the All filter hides nothing, including unclassified artifacts', () => {
  expect(filterArtifacts(ARTIFACTS, 'ALL')).toHaveLength(ARTIFACTS.length);
  expect(filterArtifacts(ARTIFACTS, 'ALL').map((a) => a.id)).toContain('6');
});

test('filtering never mutates the source list', () => {
  const before = [...ARTIFACTS];
  filterArtifacts(ARTIFACTS, 'ON_CHAIN');
  expect(ARTIFACTS).toEqual(before);
});

/* ───────────────── 4. Integrity is never fabricated ───────────────────── */

test('integrity states are labelled + coloured distinctly', () => {
  expect(integrityLabel('snapshot_sealed')).toBe('Sealed in snapshot');
  expect(integrityLabel('content_hashed')).toBe('Content hashed');
  expect(integrityLabel('unverified')).toBe('Unverified');
  expect(integrityVariant('snapshot_sealed')).toBe('success');
  expect(integrityVariant('content_hashed')).toBe('info');
  expect(integrityVariant('unverified')).toBe('neutral');
  // A content-hashed live row must never look like a sealed one.
  expect(integrityVariant('content_hashed')).not.toBe(integrityVariant('snapshot_sealed'));
});

test('an unknown integrity state fails closed to Unverified', () => {
  expect(integrityLabel('something_else')).toBe('Unverified');
  expect(integrityVariant('something_else')).toBe('neutral');
  expect(integrityLabel(null)).toBe('Unverified');
});

test('the tamper-evident mark requires BOTH immutable and snapshot_sealed', () => {
  expect(showsImmutableMark({ id: 'a', immutable: true, integrity_status: 'snapshot_sealed' })).toBe(true);
  // A backend that says "immutable" on a live row does not get the mark.
  expect(showsImmutableMark({ id: 'a', immutable: true, integrity_status: 'content_hashed' })).toBe(false);
  expect(showsImmutableMark({ id: 'a', immutable: true, integrity_status: 'unverified' })).toBe(false);
  expect(showsImmutableMark({ id: 'a', immutable: false, integrity_status: 'snapshot_sealed' })).toBe(false);
  expect(showsImmutableMark({ id: 'a' })).toBe(false);
});

test('no digest means no hash is rendered — never invented hex', () => {
  expect(shortDigest(null)).toBeNull();
  expect(shortDigest('')).toBeNull();
  expect(shortDigest('   ')).toBeNull();
  expect(shortDigest('sha256:a8f1c4f2deadbeef0011')).toBe('a8f1c4…0011');
  expect(appSource('incident-evidence-tab.tsx')).toContain('Not hashed');
});

test('a record linked only by the asset is caveated, never shown as event evidence', () => {
  expect(linkScopeCaveat({ id: 'a', metadata: { link_scope: 'ASSET' } })).toBe('Asset scope');
  expect(linkScopeCaveat({ id: 'b', metadata: { match_provenance: 'ASSET_SHARED' } })).toBe('Asset scope');
  expect(linkScopeCaveat({ id: 'c', metadata: { match_provenance: 'UNATTRIBUTED' } })).toBe('Unattributed');
});

test('an event- or incident-linked record carries no caveat', () => {
  expect(linkScopeCaveat({ id: 'a', metadata: { link_scope: 'EVENT' } })).toBeNull();
  expect(linkScopeCaveat({ id: 'b', metadata: { link_scope: 'INCIDENT' } })).toBeNull();
  expect(linkScopeCaveat({ id: 'c', metadata: { match_provenance: 'EVENT_SHARED' } })).toBeNull();
  expect(linkScopeCaveat({ id: 'd' })).toBeNull();
  expect(linkScopeCaveat({ id: 'e', metadata: null })).toBeNull();
});

test('simulator evidence is labelled, never presented as live customer evidence', () => {
  expect(artifactEvidenceSource({ id: 'a', metadata: { evidence_source: 'simulator' } }))
    .toEqual({ label: 'simulator', variant: 'info' });
  expect(artifactEvidenceSource({ id: 'b', metadata: { evidence_source: 'replay' } })?.label)
    .toBe('simulator');
  expect(artifactEvidenceSource({ id: 'c', metadata: { evidence_source: 'live_provider' } }))
    .toEqual({ label: 'live_provider', variant: 'success' });
  expect(isSimulatedArtifact({ id: 'd', metadata: { evidence_source: 'replay' } })).toBe(true);
  expect(isSimulatedArtifact({ id: 'e', metadata: { evidence_source: 'live' } })).toBe(false);
});

test('a record with no recorded provenance claims none', () => {
  expect(artifactEvidenceSource({ id: 'a' })).toBeNull();
  expect(artifactEvidenceSource({ id: 'b', metadata: {} })).toBeNull();
  expect(artifactEvidenceSource({ id: 'c', metadata: { evidence_source: '  ' } })).toBeNull();
  expect(isSimulatedArtifact({ id: 'd' })).toBe(false);
});

test('the directory renders the provenance pill on the row', () => {
  const src = appSource('incident-evidence-tab.tsx');
  expect(src).toContain('artifactEvidenceSource');
  expect(src).toContain('provenance.label');
});

test('the directory renders the weaker-link caveat on the row', () => {
  const src = appSource('incident-evidence-tab.tsx');
  expect(src).toContain('linkScopeCaveat');
  expect(src).toContain('incidentLinkScope');
  expect(styles()).toContain('.incidentLinkScope {');
});

/* ───────────────── 5. Snapshot lifecycle ──────────────────────────────── */

test('snapshot states are labelled truthfully — ready is not sealed', () => {
  expect(snapshotStatusLabel('collecting')).toBe('Evidence collecting');
  expect(snapshotStatusLabel('ready')).toBe('Evidence snapshot ready');
  expect(snapshotStatusLabel('sealed')).toBe('Evidence package sealed');
  expect(snapshotStatusLabel('failed')).toBe('Snapshot integrity failed');
  expect(snapshotStatusLabel(undefined)).toBe('Snapshot state unknown');
  expect(snapshotStatusLabel('ready')).not.toContain('sealed');
});

test('a failed snapshot is never coloured like a healthy one', () => {
  expect(snapshotStatusVariant('sealed')).toBe('success');
  expect(snapshotStatusVariant('failed')).toBe('danger');
  expect(snapshotStatusVariant('collecting')).toBe('warning');
  expect(snapshotStatusVariant(undefined)).toBe('neutral');
});

test('verification status is reported, never assumed', () => {
  const src = appSource('incident-evidence-tab.tsx');
  expect(src).toContain('snapshot.hash_verified === true');
  expect(src).toContain('snapshot.hash_verified === false');
  expect(src).toContain('Hash not verified');
  expect(src).toContain('Hash mismatch');
});

/* ───────────────── 6. Screen 9 package linkage ────────────────────────── */

test('the package link appears only when a package actually exists', () => {
  expect(hasEvidencePackage({ available: true, package_id: 'pkg-1' })).toBe(true);
  expect(hasEvidencePackage({ available: false })).toBe(false);
  expect(hasEvidencePackage({ available: true })).toBe(false); // no id -> no link
  expect(hasEvidencePackage(null)).toBe(false);
  expect(hasEvidencePackage(undefined)).toBe(false);
});

test('an absent package states so rather than pretending one exists', () => {
  expect(evidencePackageAbsenceLabel({ available: false })).toBe('Evidence package not generated.');
  expect(evidencePackageAbsenceLabel({ available: false, reason: 'unavailable' }))
    .toContain('unavailable');
});

test('Screen 7 links to Screen 9 and never re-implements packaging', () => {
  const src = appSource('incident-evidence-tab.tsx');
  expect(src).toContain('View Evidence Package');
  expect(src).toContain('/evidence?package_id=');
  // No packaging, hashing, manifest or download implementation lives here.
  expect(src).not.toContain('manifest');
  expect(src).not.toContain('.zip');
  expect(src).not.toContain('/download');
});

/* ───────────────── 7. Policy forensics ────────────────────────────────── */

test('the deterministic decision drives the pill, DENY and ALLOW distinctly', () => {
  expect(policyDecisionVariant('DENY')).toBe('danger');
  expect(policyDecisionVariant('ALLOW')).toBe('success');
  expect(policyDecisionVariant('deny')).toBe('danger');
  expect(policyDecisionVariant(null)).toBe('neutral');
  expect(policyDecisionVariant('SOMETHING')).toBe('neutral');
});

test('a Screen 11 simulation never appears as the verdict that gated the response', () => {
  const evaluations = [
    { evaluation_id: 'a', decision: 'DENY', simulation: false },
    { evaluation_id: 'b', decision: 'ALLOW', simulation: true },
  ];
  expect(enforcementEvaluations(evaluations).map((e) => e.evaluation_id)).toEqual(['a']);
  expect(enforcementEvaluations([])).toEqual([]);
  expect(enforcementEvaluations(null)).toEqual([]);
});

test('policy forensics renders the backend decision, reason codes and required roles', () => {
  const src = appSource('incident-evidence-tab.tsx');
  expect(src).toContain('evaluation.decision');
  expect(src).toContain('evaluation.reason_codes');
  expect(src).toContain('evaluation.required_approvals');
  expect(src).toContain('evaluation.policy_version');
  expect(src).toContain('No decision recorded');
});

/* ───────────────── 8. Forensic timeline ───────────────────────────────── */

const EVENTS: IncidentTimelineEvent[] = [
  { id: 'c', occurred_at: '2026-01-01T10:42:18.382000+00:00', stage: 'incident_created' },
  { id: 'a', occurred_at: '2026-01-01T10:42:17.920000+00:00', stage: 'state_drift_detected' },
  { id: 'b', occurred_at: '2026-01-01T10:42:18.001000+00:00', stage: 'operational_anomaly' },
];

test('timeline sorts by canonical server timestamp, not array position', () => {
  expect(sortTimelineEvents(EVENTS).map((e) => e.id)).toEqual(['a', 'b', 'c']);
});

test('timeline sorting is stable and never mutates the input', () => {
  const before = [...EVENTS];
  sortTimelineEvents(EVENTS);
  expect(EVENTS).toEqual(before);
  const tied = sortTimelineEvents([
    { id: 'z', occurred_at: '2026-01-01T10:00:00.000Z' },
    { id: 'a', occurred_at: '2026-01-01T10:00:00.000Z' },
  ]);
  expect(tied.map((e) => e.id)).toEqual(['a', 'z']);
});

test('an event without a timestamp sorts last rather than being dropped', () => {
  const sorted = sortTimelineEvents([
    { id: 'no-time' },
    { id: 'timed', occurred_at: '2026-01-01T10:00:00Z' },
  ]);
  expect(sorted.map((e) => e.id)).toEqual(['timed', 'no-time']);
});

test('millisecond precision is shown only when the record carries it', () => {
  expect(formatForensicTime('2026-01-01T10:42:18.382000+00:00')).toMatch(/^\d{2}:\d{2}:\d{2}\.\d{3}$/);
  // A second-precision record is not padded with a fake .000
  expect(formatForensicTime('2026-01-01T10:42:18+00:00')).toMatch(/^\d{2}:\d{2}:\d{2}$/);
  expect(formatForensicTime(null)).toBe('Unknown');
  expect(formatForensicTime('not-a-date')).toBe('Unknown');
});

test('the calendar day is stated whenever it changes, so a run of times cannot span days silently', () => {
  const headings = timelineDayHeadings([
    { id: 'a', occurred_at: '2026-01-01T23:59:00Z' },
    { id: 'b', occurred_at: '2026-01-01T23:59:30Z' },
    { id: 'c', occurred_at: '2026-01-03T00:01:00Z' },
  ]);
  expect(headings[0]).not.toBeNull();       // first event always names its day
  expect(headings[1]).toBeNull();           // same day -> no repeat
  expect(headings[2]).not.toBeNull();       // day changed -> stated again
  expect(headings[2]).not.toBe(headings[0]);
});

test('an event without a timestamp gets no fabricated day heading', () => {
  expect(timelineDayHeadings([{ id: 'a' }])).toEqual([null]);
  expect(forensicDay(null)).toBeNull();
  expect(forensicDay('not-a-date')).toBeNull();
  expect(forensicDay('2026-01-01T10:00:00Z')).not.toBeNull();
});

test('both forensic views state the calendar day alongside the time', () => {
  expect(appSource('incident-forensic-timeline.tsx')).toContain('timelineDayHeadings');
  expect(appSource('incident-evidence-tab.tsx')).toContain('forensicDay(artifact.collected_at)');
});

test('attribution never turns a system event into a human one, and AI is recommend-only', () => {
  expect(actorLabel({ id: '1', actor_type: 'system', source: 'Decoda detection engine' }))
    .toBe('Decoda detection engine');
  expect(actorLabel({ id: '2', actor_type: 'user', actor_id: 'abcd1234-5678' }))
    .toBe('Operator abcd1234');
  expect(actorLabel({ id: '3', actor_type: 'ai' })).toBe('AI (recommend only)');
  expect(actorLabel({ id: '4' })).toBe('System');
});

/* ───────────────── 9. Load / empty / error states ─────────────────────── */

test('load state distinguishes unauthorized / not found / error / empty / ready', () => {
  expect(loadStateFor(200, true)).toBe('ready');
  expect(loadStateFor(200, false)).toBe('empty');
  expect(loadStateFor(401, false)).toBe('unauthorized');
  expect(loadStateFor(403, false)).toBe('unauthorized');
  expect(loadStateFor(404, false)).toBe('not_found');
  expect(loadStateFor(500, false)).toBe('error');
  expect(loadStateFor(null, false)).toBe('error');
});

test('empty-state copy is scoped to the selected domain and never suggests safety', () => {
  expect(emptyEvidenceMessage('ALL')).toBe('No evidence has been collected for this incident.');
  expect(emptyEvidenceMessage('OPERATIONAL'))
    .toBe('No operational evidence has been collected for this incident.');
  for (const domain of EVIDENCE_DOMAINS) {
    const message = emptyEvidenceMessage(domain);
    expect(message).toContain('No ');
    expect(message).not.toMatch(/healthy|clean|safe|all clear/i);
  }
});

test('the Evidence tab implements every required UI state', () => {
  const src = appSource('incident-evidence-tab.tsx');
  expect(src).toContain('Loading incident evidence…');   // loading
  expect(src).toContain('emptyEvidenceMessage');          // no artifacts
  expect(src).toContain('Evidence is unavailable');       // backend unavailable
  expect(src).toContain('Partial evidence');              // partial evidence
  expect(src).toContain('do not have permission');        // unauthorized
  expect(src).toContain('could not be found');            // not found
  expect(src).toContain('Retry');                         // error + retry
});

test('the forensic timeline implements every required UI state', () => {
  const src = appSource('incident-forensic-timeline.tsx');
  expect(src).toContain('Loading incident timeline…');
  expect(src).toContain('No lifecycle events have been recorded');
  expect(src).toContain('Partial history');
  expect(src).toContain('Timeline unavailable');
  expect(src).toContain('do not have permission');
});

/* ───────────────── 10. No fabricated / reference-design data ──────────── */

const SCREEN7_SOURCES = [
  'incident-evidence-tab.tsx',
  'incident-forensic-timeline.tsx',
  'incident-forensics-presentation.ts',
];

test('no reference-design value is hard-coded into Screen 7 production code', () => {
  for (const file of SCREEN7_SOURCES) {
    const src = appSource(file);
    expect(src, file).not.toMatch(/INC-2026-017/);
    expect(src, file).not.toMatch(/EV-2026-017/);
    expect(src, file).not.toMatch(/POL-MINT-007/);
    expect(src, file).not.toMatch(/US Treasury Bond/);
    expect(src, file).not.toMatch(/subscription_record\.json/);
    expect(src, file).not.toMatch(/0x[0-9a-f]{8,}/i);   // no sample tx hash / address
    expect(src, file).not.toContain('Math.random');
  }
});

test('the Evidence tab reads the real per-incident endpoint through the proxy', () => {
  const src = appSource('incident-evidence-tab.tsx');
  expect(src).toContain('/incidents/${encodeURIComponent(incidentId)}/evidence');
  expect(src).toContain('authHeaders()');
  expect(src).toContain("const API_PROXY_BASE = '/api'");
});

test('artifact table columns and rows come from backend fields only', () => {
  const src = appSource('incident-evidence-tab.tsx');
  for (const field of ['file_name', 'artifact_type', 'domain', 'source', 'collected_at',
    'content_sha256', 'integrity_status']) {
    expect(src, field).toContain(`artifact.${field}`);
  }
  expect(src).toContain('data?.artifacts ?? []');
});

test('artifact type labels replace raw snake_case keys without inventing meaning', () => {
  expect(artifactTypeLabel('subscription_record')).toBe('Subscription Record');
  expect(artifactTypeLabel('policy_decision')).toBe('Policy Decision');
  expect(artifactTypeLabel('a_brand_new_type')).toBe('A Brand New Type');
  expect(artifactTypeLabel(null)).toBe('Artifact');
  expect(artifactTypeLabel('')).toBe('Artifact');
});

/* ───────────────── 11. Wiring + layout ────────────────────────────────── */

test('both Screen 7 surfaces render the forensic evidence directory and timeline', () => {
  for (const file of ['incidents-panel.tsx', 'incident-case-file-tabs.tsx']) {
    const src = appSource(file);
    expect(src, file).toContain('IncidentEvidenceTab');
    expect(src, file).toContain('IncidentForensicTimeline');
  }
});

test('the timeline response is fetched once and yields both shapes', () => {
  for (const file of ['incidents-panel.tsx', 'incident-case-file-tabs.tsx']) {
    const src = appSource(file);
    expect(src, file).toContain('json?.timeline ?? []');
    expect(src, file).toContain('setForensicTimeline');
    expect(src, file).toContain('loadStateFor(');
  }
});

test('the forensic evidence proxy route exists and targets the backend', () => {
  const route = fs.readFileSync(
    path.join(__dirname, '..', 'app', 'api', 'incidents', '[incidentId]', 'evidence', 'route.ts'),
    'utf-8',
  );
  expect(route).toContain('/incidents/${encodeURIComponent(incidentId)}/evidence');
  expect(route).toContain('proxyJsonToBackend');
});

test('the incident header shows the real asset, category and detection type', () => {
  const src = appSource('forensic-investigator-panel.tsx');
  expect(src).toContain('incident.asset_label');
  expect(src).toContain('incident.detection_category');
  expect(src).toContain('incident.detection_type');
  // Absent facts are stated, never substituted with a placeholder.
  expect(src).toContain('Not classified');
  expect(src).toContain('Not available');
});

test('the artifact table scrolls inside its own wrapper, never the page', () => {
  const css = styles();
  expect(css).toContain('.incidentEvidenceTable { overflow-x: auto; }');
  expect(css).toContain('.incidentEvidenceTable table { min-width:');
  // Domain cards reflow rather than overflowing on narrow widths.
  expect(css).toContain('.incidentEvidenceDomains {');
  expect(css).toContain('grid-template-columns: repeat(auto-fit');
});

test('the Screen 7 forensic styles introduce no gradients, glows or animations', () => {
  const css = styles();
  const block = css.slice(css.indexOf('.incidentEvidenceDomains {'));
  expect(block).not.toMatch(/linear-gradient|radial-gradient/);
  expect(block).not.toMatch(/@keyframes|animation:/);
});
