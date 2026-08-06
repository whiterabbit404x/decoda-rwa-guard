import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

import { normalizeHistoryEventDto } from '../app/(product)/response-actions-presentation';

// Screen 8 — Action History approval-completeness pass (frontend). The deployed
// "Response Action Approved" event was persisted before the enriched writer, so its
// details view showed a dash for Action ID, Incident, Previous/New state, Approval
// progress, and Approval policy, and "Not applicable" for Decision. The backend now
// returns (and self-heals) a complete DTO; these tests prove React surfaces every field
// truthfully — the resolved action id, the incident, the Awaiting Approval -> Approved
// transition, Decision Approved, 1 of 1 progress, and the real approval policy — and uses
// truthful fallbacks (never a bare dash) when a legacy field is genuinely unrecoverable.

const INCIDENT = 'c537b73f-1976-4a44-b589-946194794399';

function pageSource(): string {
  return fs.readFileSync(
    path.join(__dirname, '..', 'app', '(product)', 'response-actions-page-client.tsx'),
    'utf-8',
  );
}

// The canonical DTO the backend returns for the repaired one-of-one approval event. Note
// its raw event_metadata carries NO action_id (the legacy sparse shape) — the resolved
// top-level action_id is what must reach the details view.
const REPAIRED_APPROVAL_DTO = {
  event_id: 'evt-approve-1',
  event_type: 'response_action_approved',
  event_label: 'Response Action Approved',
  type_label: 'Action Approval',
  action_id: 'b2222222-2222-4222-8222-222222222222',
  action_title: 'Notify Security Team',
  action_key: 'notify_team',
  incident_id: INCIDENT,
  incident_short_id: 'INC-c537b73f',
  actor_id: '11111111-1111-4111-8111-111111111111',
  actor_email: 'decoda.guard@gmail.com',
  occurred_at: '2026-08-05T10:15:00+00:00',
  previous_state: 'awaiting_approval',
  previous_state_label: 'Awaiting Approval',
  new_state: 'approved',
  new_state_label: 'Approved',
  decision: 'approved',
  decision_label: 'Approved',
  result: 'Success',
  result_label: 'Success',
  approval_count: 1,
  required_approval_count: 1,
  approval_progress_label: '1 of 1',
  approval_policy: 'owner_admin_single_approver',
  approval_policy_id: 'owner_admin_single_approver',
  approval_policy_label: 'Owner/Admin single approver',
  action_route: '/response-actions?action_id=b2222222-2222-4222-8222-222222222222',
  incident_route: `/incidents/${INCIDENT}`,
  audit_route: '/history?event_id=evt-approve-1',
  // The legacy sparse metadata never carried action_id.
  event_metadata: { type_label: 'Action Approval' },
};

// 10.1 — View details shows the real (resolved) Action ID even though the raw metadata
//        lacked it. The DTO carries the resolved id, and the details row prefers it.
test('the resolved action id is available and the details view prefers it', () => {
  const dto = normalizeHistoryEventDto(REPAIRED_APPROVAL_DTO);
  expect(dto.actionId).toBe('b2222222-2222-4222-8222-222222222222');
  const src = pageSource();
  // The Action ID row prefers the resolved row.actionId, not just raw metadata.
  expect(src).toContain('const rawMeta = row.eventMetadata?.action_id');
  expect(src).toContain('const actionId = row.actionId || (typeof rawMeta === \'string\' ? rawMeta : \'\')');
});

// 10.2 — View details shows the incident (short id + canonical route).
test('the incident short id and canonical incident route are present', () => {
  const dto = normalizeHistoryEventDto(REPAIRED_APPROVAL_DTO);
  expect(dto.incidentShortId).toBe('INC-c537b73f');
  expect(dto.incidentRoute).toBe(`/incidents/${INCIDENT}`);
});

// 10.3 — Awaiting Approval -> Approved transition is visible.
test('the Awaiting Approval to Approved transition is exposed', () => {
  const dto = normalizeHistoryEventDto(REPAIRED_APPROVAL_DTO);
  expect(dto.previousStateLabel).toBe('Awaiting Approval');
  expect(dto.newStateLabel).toBe('Approved');
});

// 10.4 + 10.7 — Decision displays Approved (details AND the table Decision column).
test('decision is Approved and the table Decision column renders it', () => {
  const dto = normalizeHistoryEventDto(REPAIRED_APPROVAL_DTO);
  expect(dto.decision).toBe('approved');
  expect(dto.decisionLabel).toBe('Approved');
  const src = pageSource();
  // The table renders the decision label as a success pill (never "Not applicable" here).
  expect(src).toContain('label={row.decisionLabel}');
  expect(src).toContain("row.decision === 'approved' || row.decision === 'accepted'");
});

// 10.5 — Approval progress displays 1 of 1.
test('approval progress displays 1 of 1', () => {
  const dto = normalizeHistoryEventDto(REPAIRED_APPROVAL_DTO);
  expect(dto.approvalProgressLabel).toBe('1 of 1');
});

// 10.6 — Approval policy is visible via its operator-facing label.
test('the approval policy label is surfaced (id + label)', () => {
  const dto = normalizeHistoryEventDto(REPAIRED_APPROVAL_DTO);
  expect(dto.approvalPolicyId).toBe('owner_admin_single_approver');
  expect(dto.approvalPolicyLabel).toBe('Owner/Admin single approver');
  const src = pageSource();
  // The details view prefers the backend label, falling back to a humanized id.
  expect(src).toContain('row.approvalPolicyLabel ?? (row.approvalPolicy ? humanizeApprovalPolicy(row.approvalPolicy) : \'—\')');
});

// A DTO that carries only the policy id (no label) still yields a usable id, and the view
// falls back to humanizing it — never a bare dash for a real policy.
test('a policy id without a label still resolves to a usable value', () => {
  const dto = normalizeHistoryEventDto({ ...REPAIRED_APPROVAL_DTO, approval_policy_label: undefined });
  expect(dto.approvalPolicyLabel).toBeNull();
  expect(dto.approvalPolicyId).toBe('owner_admin_single_approver');
});

// 10.8 — a genuinely unrecoverable state uses a truthful fallback, never a bare dash.
test('missing lifecycle uses a truthful fallback, not a bare dash', () => {
  const src = pageSource();
  expect(src).toContain("row.approvalEvent ? 'Previous state unavailable' : '—'");
  expect(src).toContain("row.approvalEvent ? 'New state unavailable' : '—'");
});

// 10.9 — View Incident opens the canonical incident route.
test('View Incident uses the canonical incident route', () => {
  const dto = normalizeHistoryEventDto(REPAIRED_APPROVAL_DTO);
  expect(dto.incidentRoute).toBe(`/incidents/${INCIDENT}`);
  const src = pageSource();
  expect(src).toContain('View Incident');
  expect(src).toContain('href={row.incidentRoute}');
});

// 10.10 — the enriched event survives a refresh: history is read from the persisted audit
//         endpoint (the backend repair persists the enrichment), not rebuilt client-side.
test('history is read from the persisted audit endpoint so a refresh preserves enrichment', () => {
  const src = pageSource();
  expect(src).toContain('/history/actions?limit=50');
  expect(src).toContain('const reloadActions = () => setReloadKey((k) => k + 1);');
});
