/**
 * Screen 9 — EV-2026-004 "Included Artifacts" truthfulness.
 *
 * The deployed detail card previously rendered a HARDCODED six-item checklist
 * (REQUIRED_ARTIFACTS) whose green/red state came from the list-row `includes` /
 * `missing_artifacts` fields (or, absent those, from `ready`). For EV-2026-004 that
 * showed green checks beside Telemetry Snapshot, Detection Event, Alert, Incident
 * Timeline, Response Action and Audit Log — evidence the package does NOT actually
 * contain. The authoritative detail response says the opposite:
 *
 *   incident_trace.available_sections: incident, response_action, export_metadata
 *   incident_trace.missing_sections:   alerts, detections, telemetry_evidence, audit_log
 *   completeness.missing_codes also lists asset_identity + investigation_timeline
 *   (source evidence) and file_hashes + manifest_hash (derived integrity artifacts).
 *
 * These regression tests lock the truthful inventory: available / missing / derived are
 * driven ONLY by the canonical detail response (incident_trace + completeness), a green
 * check never appears beside missing evidence, and the panel renders the canonical split
 * rather than the hardcoded checklist.
 */
import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

import {
  resolveEvidenceActionState,
  resolveIncludedArtifacts,
  resolveRecoveryRequirements,
  type CompletenessRequirementSource,
  type IncidentTraceSource,
} from '../app/evidence-recovery-actions';

const PANEL = 'app/evidence-audit-panel.tsx';
function readPanel(): string {
  return fs.readFileSync(path.join(__dirname, '..', PANEL), 'utf-8');
}

/** EV-2026-004 canonical incident_trace (from the package's own stored summary). */
const EV004_INCIDENT_TRACE: IncidentTraceSource = {
  available_sections: ['incident', 'response_action', 'export_metadata'],
  missing_sections: ['alerts', 'detections', 'telemetry_evidence', 'audit_log'],
};

/** EV-2026-004 canonical completeness (20% critical). */
const EV004_COMPLETENESS: CompletenessRequirementSource = {
  categories: [
    { code: 'incident_identity', label: 'Incident identity and timestamps', required: true, status: 'present' },
    { code: 'original_alert', label: 'Original alert', required: true, status: 'missing' },
    { code: 'detection_provenance', label: 'Detection provenance', required: true, status: 'missing' },
    { code: 'telemetry_reference', label: 'Raw telemetry references', required: true, status: 'missing' },
    { code: 'asset_identity', label: 'Asset identity', required: true, status: 'missing' },
    { code: 'investigation_timeline', label: 'Investigation timeline', required: true, status: 'missing' },
    { code: 'audit_events', label: 'Audit events', required: true, status: 'missing' },
    { code: 'file_hashes', label: 'File hashes (SHA-256)', required: true, status: 'missing' },
    { code: 'manifest_hash', label: 'Manifest hash', required: true, status: 'missing' },
  ],
  missing_codes: [
    'original_alert',
    'detection_provenance',
    'telemetry_reference',
    'asset_identity',
    'investigation_timeline',
    'audit_events',
    'file_hashes',
    'manifest_hash',
  ],
};

/* ── 1. Canonical split for EV-2026-004 ───────────────────────────────────────── */

test('artifacts: EV-2026-004 splits into AVAILABLE / MISSING / DERIVED exactly per the canonical response', () => {
  const inv = resolveIncludedArtifacts(EV004_INCIDENT_TRACE, EV004_COMPLETENESS);

  // AVAILABLE — only what the package actually contains.
  expect(inv.available.map((a) => a.code)).toEqual(['incident', 'response_action', 'export_metadata']);
  expect(inv.available.map((a) => a.label)).toEqual(['Incident', 'Response Action', 'Export Metadata']);

  // MISSING — the four missing sections PLUS the two completeness-only source gaps
  // (asset identity, investigation timeline). Concise section labels.
  expect(inv.missing.map((a) => a.code)).toEqual([
    'original_alert',
    'detection_provenance',
    'telemetry_evidence',
    'asset_identity',
    'investigation_timeline',
    'audit_events',
  ]);
  expect(inv.missing.map((a) => a.label)).toEqual([
    'Original Alert',
    'Detection Provenance',
    'Raw Telemetry Evidence',
    'Asset Identity',
    'Investigation Timeline',
    'Audit Events',
  ]);

  // DERIVED — the two integrity artifacts generated after recovery (not yet available).
  expect(inv.derived.map((a) => a.code)).toEqual(['file_hashes', 'manifest_hash']);
  // Same split the recovery panel uses, so the two groups can never disagree.
  expect(inv.derived.map((a) => a.code)).toEqual(
    resolveRecoveryRequirements(EV004_COMPLETENESS).derived.map((d) => d.code),
  );
});

/* ── 2. A green check NEVER appears beside canonical missing evidence ──────────── */

test('artifacts: no missing section is ever classified as available (no green check for missing evidence)', () => {
  const inv = resolveIncludedArtifacts(EV004_INCIDENT_TRACE, EV004_COMPLETENESS);
  const availableCodes = new Set(inv.available.map((a) => a.code));

  // None of the previously green-checked artifacts is available.
  for (const missingCode of ['original_alert', 'detection_provenance', 'telemetry_evidence', 'audit_events']) {
    expect(availableCodes.has(missingCode)).toBe(false);
  }
  // Nothing appears in BOTH available and missing.
  const missingCodes = new Set(inv.missing.map((a) => a.code));
  for (const code of availableCodes) expect(missingCodes.has(code)).toBe(false);
  // Derived integrity artifacts are never presented as available source evidence.
  for (const d of inv.derived) expect(availableCodes.has(d.code)).toBe(false);
});

/* ── 3. available_sections / missing_sections drive the UI ─────────────────────── */

test('artifacts: available_sections and missing_sections drive availability, not any hardcoded list', () => {
  // Move telemetry from missing → available: the inventory must follow the canonical trace.
  const trace: IncidentTraceSource = {
    available_sections: ['incident', 'response_action', 'export_metadata', 'telemetry'],
    missing_sections: ['alerts', 'detections', 'audit_log'],
  };
  const inv = resolveIncludedArtifacts(trace, EV004_COMPLETENESS);
  expect(inv.available.map((a) => a.code)).toContain('telemetry_evidence');
  expect(inv.missing.map((a) => a.code)).not.toContain('telemetry_evidence');

  // A fully complete chain → every catalog section reports available (all green).
  const completeTrace: IncidentTraceSource = {
    available_sections: [
      'incident', 'alert', 'detection', 'telemetry', 'asset_context', 'response_action', 'audit_log', 'export_metadata',
    ],
    missing_sections: [],
  };
  const complete = resolveIncludedArtifacts(completeTrace, { missing_codes: [] });
  expect(complete.missing).toEqual([]);
  expect(complete.derived).toEqual([]);
  expect(complete.available.map((a) => a.code)).toEqual(
    expect.arrayContaining(['incident', 'original_alert', 'detection_provenance', 'telemetry_evidence', 'audit_events']),
  );

  // Empty canonical response → nothing is invented as present OR missing.
  expect(resolveIncludedArtifacts(null, null)).toEqual({ available: [], missing: [], derived: [] });
  expect(resolveIncludedArtifacts({}, {})).toEqual({ available: [], missing: [], derived: [] });
});

/* ── 4. The panel renders the canonical split, not the hardcoded checklist ─────── */

test('panel: Included Artifacts renders from resolveIncludedArtifacts, never the hardcoded REQUIRED_ARTIFACTS checklist', () => {
  const source = readPanel();

  // Driven by the canonical detail response (incident_trace + completeness).
  expect(source).toContain('resolveIncludedArtifacts(');
  expect(source).toContain('detail?.incident_trace');
  expect(source).toContain('includedArtifacts.available.map');
  expect(source).toContain('includedArtifacts.missing.map');
  expect(source).toContain('includedArtifacts.derived.map');
  // The three truthful groups.
  expect(source).toContain('Generated integrity artifacts — not yet available');

  // The old hardcoded checklist is gone: it mapped REQUIRED_ARTIFACTS through
  // artifactPresent(), which returned `ready` (or the legacy `includes`) and so showed
  // green checks for missing evidence.
  expect(source).not.toContain('const present = artifactPresent(artifact)');
  expect(source).not.toContain('function artifactPresent');
});

/* ── 5. Recovery-required blocker + absent generate/regenerate + View Incident ──── */

test('panel + gate: recovery_required shows the backend blocker; generate/regenerate stay absent; View Incident remains', () => {
  // The EV-2026-004 detail: recovery required, both recovery actions denied.
  const gate = resolveEvidenceActionState(
    {
      is_manifest_missing: true,
      integrity_status: 'needs_evidence',
      recovery_required: true,
      recovery_state: 'evidence_required',
      recovery_blocked_reason: 'Collect the missing required evidence before regenerating this package.',
      allowed_actions: {
        view: true,
        download: true,
        download_manifest: false,
        verify: false,
        generate_manifest: false,
        regenerate_package: false,
      },
    },
    true,
  );
  expect(gate.recoveryRequired).toBe(true);
  expect(gate.recoveryBlocked).toBe(true);
  expect(gate.recoveryBlockedReason).toBe(
    'Collect the missing required evidence before regenerating this package.',
  );
  // Generate Manifest / Regenerate Package remain absent because both backend actions are false.
  expect(gate.showGenerate).toBe(false);
  expect(gate.showRegenerate).toBe(false);

  const source = readPanel();
  // The stable "Recovery required" blocker with the backend reason.
  expect(source).toContain('aria-label="Recovery required"');
  expect(source).toContain('Recovery required');
  expect(source).toContain('recoveryBlockedReason');
  // View Incident routed to the canonical linked-incident route.
  expect(source).toContain('View Incident');
  expect(source).toContain('href={incidentHref}');
  expect(source).toContain('`/incidents/${encodeURIComponent(pkg.incident_id)}?tab=evidence`');
});
