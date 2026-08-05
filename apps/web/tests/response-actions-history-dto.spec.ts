import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

import {
  formatExactTimestamp,
  formatRelativeTime,
  historyOutcome,
  isCanonicalHistoryDto,
  normalizeHistoryEventDto,
} from '../app/(product)/response-actions-presentation';

// Canonical Action History DTO consumption (Screen 8). The BACKEND now returns each
// history row as a fully-formed, self-describing DTO; React renders those fields
// directly. These tests prove the reported problems are fixed: the Action Approval row
// shows a human EVENT label, the readable action TITLE, the actor ACCOUNT identity (not
// a UUID), the Awaiting Approval -> Approved transition, Decision Approved, 1 of 1
// progress, an accessible exact timestamp, canonical routes, and an event-details view —
// while the older AI recommendation review stays a separate, visible event.

function pageSource(): string {
  return fs.readFileSync(
    path.join(__dirname, '..', 'app', '(product)', 'response-actions-page-client.tsx'),
    'utf-8',
  );
}

// The canonical DTO the backend returns for the successful approval event.
const APPROVAL_DTO = {
  event_id: 'evt-approve-1',
  event_type: 'response_action_approved',
  event_label: 'Response Action Approved',
  type_label: 'Action Approval',
  action_id: 'act-9',
  action_title: 'Notify Security Team',
  action_key: 'notify_team',
  incident_id: 'c537b73f-1976-4a44-b589-946194794399',
  incident_short_id: 'INC-c537b73f',
  actor_id: '11111111-1111-4111-8111-111111111111',
  actor_display_name: 'Decoda Guard',
  actor_email: 'decoda.guard@gmail.com',
  actor_type: 'user',
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
  evidence_source: 'runtime',
  evidence_source_label: 'Runtime record',
  approval_policy: 'owner_admin_single_approver',
  action_route: '/response-actions?action_id=act-9',
  incident_route: '/incidents/c537b73f-1976-4a44-b589-946194794399',
  evidence_route: '/evidence?incident_id=c537b73f-1976-4a44-b589-946194794399',
  audit_route: '/history?event_id=evt-approve-1',
  event_metadata: { action_id: 'act-9', type_label: 'Action Approval' },
};

// 10.1 — the Action Approval row renders "Response Action Approved".
test('canonical DTO row renders the Response Action Approved event label', () => {
  const dto = normalizeHistoryEventDto(APPROVAL_DTO);
  expect(dto.eventLabel).toBe('Response Action Approved');
  expect(dto.typeLabel).toBe('Action Approval');
});

// 10.2 — the actor displays a user-facing identity, NEVER the raw UUID.
test('canonical DTO row displays the actor account identity, not a UUID', () => {
  const dto = normalizeHistoryEventDto(APPROVAL_DTO);
  expect(dto.actorEmail).toBe('decoda.guard@gmail.com');
  expect(dto.actorDisplayName).toBe('Decoda Guard');
  // The UUID is available only as the technical actor id.
  expect(dto.actorId).toBe('11111111-1111-4111-8111-111111111111');
  expect(dto.actorEmail).not.toBe(dto.actorId);
});

// 10.3 — the Awaiting Approval -> Approved transition is present.
test('canonical DTO row exposes the Awaiting Approval to Approved transition', () => {
  const dto = normalizeHistoryEventDto(APPROVAL_DTO);
  expect(dto.previousStateLabel).toBe('Awaiting Approval');
  expect(dto.newStateLabel).toBe('Approved');
});

// 10.4 + 10.5 — Decision Approved + 1 of 1 progress + readable action title.
test('canonical DTO row exposes decision, progress, and a readable action title', () => {
  const dto = normalizeHistoryEventDto(APPROVAL_DTO);
  expect(dto.decisionLabel).toBe('Approved');
  expect(dto.approvalProgressLabel).toBe('1 of 1');
  expect(dto.actionTitle).toBe('Notify Security Team');
  // Never a truncated generic label like "Response Action A…".
  expect(dto.actionTitle).not.toContain('Response Action A');
});

// 10.6 — the exact timestamp is derived from the PERSISTED time (not page-load), and a
// readable relative time is available for the table.
test('exact + relative time come from the persisted timestamp', () => {
  const dto = normalizeHistoryEventDto(APPROVAL_DTO);
  expect(dto.occurredAt).toBe('2026-08-05T10:15:00+00:00');
  const exact = formatExactTimestamp(dto.occurredAt);
  expect(exact).toContain('2026');
  expect(exact).toContain('August');
  // Relative time is stable relative to a fixed "now" (never the current wall clock).
  const now = Date.parse('2026-08-05T13:15:00+00:00');
  expect(formatRelativeTime(dto.occurredAt, now)).toBe('3h ago');
});

// 10.7 — an AI recommendation review remains a SEPARATE, distinctly-typed event.
test('a recommendation review DTO stays a separate event type', () => {
  const reviewDto = normalizeHistoryEventDto({
    event_id: 'evt-review-1',
    event_type: 'recommendation_reviewed',
    event_label: 'AI Recommendation Reviewed',
    type_label: 'AI Recommendation Review',
    decision: 'accepted',
    decision_label: 'Accepted',
    occurred_at: '2026-07-14T09:00:00+00:00',
    actor_email: 'reviewer@example.com',
  });
  expect(reviewDto.eventType).toBe('recommendation_reviewed');
  expect(reviewDto.eventLabel).toBe('AI Recommendation Reviewed');
  // It is NOT a response-action approval.
  expect(reviewDto.eventLabel).not.toBe('Response Action Approved');
});

// 10.8 — internal (snake_case / dotted) event names are never surfaced.
test('internal event keys never appear as the displayed label', () => {
  const dto = normalizeHistoryEventDto(APPROVAL_DTO);
  expect(dto.eventLabel).not.toContain('_');
  expect(dto.eventLabel).not.toContain('.');
  expect(dto.typeLabel).not.toContain('_');
  // A pre-DTO legacy payload still maps to a human label, never a raw enum.
  const legacy = normalizeHistoryEventDto({
    id: 'legacy-1',
    action_type: 'response_action.approved',
    object_type: 'response_action',
    timestamp: '2026-08-05T10:15:00+00:00',
    details_json: { event_type: 'response_action_approved' },
  });
  expect(isCanonicalHistoryDto({ id: 'x' })).toBe(false);
  expect(legacy.eventLabel).toBe('Response Action Approved');
  expect(legacy.eventLabel).not.toContain('_');
});

// 10.9 — the event-details view is wired up (open/close a selected row).
test('the page wires an event-details view for a selected history row', () => {
  const src = pageSource();
  expect(src).toContain('HistoryEventDetails');
  expect(src).toContain('setSelectedHistoryId');
  expect(src).toContain('selectedHistoryRow');
  // Details include the exact timestamp + the safe explanation, never a secret.
  expect(src).toContain('formatExactTimestamp(row.time)');
  expect(src).toContain('historyEventExplanation');
});

// 10.10 — View Action + View Incident + View Audit Event use the canonical routes.
test('View Action / View Incident / View Audit Event use canonical routes', () => {
  const dto = normalizeHistoryEventDto(APPROVAL_DTO);
  expect(dto.actionRoute).toBe('/response-actions?action_id=act-9');
  expect(dto.incidentRoute).toBe('/incidents/c537b73f-1976-4a44-b589-946194794399');
  expect(dto.auditRoute).toBe('/history?event_id=evt-approve-1');
  const src = pageSource();
  expect(src).toContain('View Action');
  expect(src).toContain('View Incident');
  expect(src).toContain('View Audit Event');
  expect(src).toContain('href={row.actionRoute}');
  expect(src).toContain('href={row.incidentRoute}');
  expect(src).toContain('href={row.auditRoute}');
});

// 10.11 — history is loaded from the persisted audit endpoint, so a refresh preserves it.
test('history is loaded from the persisted audit endpoint (survives refresh)', () => {
  const src = pageSource();
  expect(src).toContain('/history/actions?limit=50');
  expect(src).toContain('const reloadActions = () => setReloadKey((k) => k + 1);');
  expect(src).toContain('reloadKey]');
});

// 10.12 — Action History stays visible while "Requires Approval" is selected: the
// history filter depends ONLY on the search + dedicated history filters.
test('history filtering never inherits the Requires Approval active-action predicate', () => {
  const src = pageSource();
  const start = src.indexOf('const filteredHistory');
  const body = src.slice(start, src.indexOf('}, [historyRows', start));
  expect(body).toContain('historyRows.filter');
  expect(body).not.toContain('approvalFilter');
  expect(body).not.toContain('requiresApproval');
  expect(body).not.toContain('matchesApproval');
});

// The Outcome cell is event-specific and never a bare, context-free dash.
test('historyOutcome is event-specific, never a context-free dash', () => {
  expect(historyOutcome({ eventType: 'response_action_approved', decision: 'approved' })).toBe(
    'Approval quorum reached',
  );
  expect(historyOutcome({ recordType: 'ai_recommendation_review', decision: 'accepted' })).toBe(
    'Recommendation accepted',
  );
  expect(historyOutcome({ eventType: 'response_action_executed' })).toBe('Executed successfully');
  expect(
    historyOutcome({ eventType: 'response_action_approval_blocked', resultLabel: 'MFA Required' }),
  ).toBe('MFA Required');
  expect(historyOutcome({ eventType: 'incident_timeline_note_added' })).toBe('Not applicable');
});

// The event metadata a legacy fallback exposes must never carry an auth secret (the
// server strips it; the fallback must not reintroduce one from a raw payload).
test('legacy fallback never surfaces a raw enum as the actor label', () => {
  const dto = normalizeHistoryEventDto({
    id: 'legacy-2',
    action_type: 'response_action.approved',
    object_type: 'response_action',
    actor_id: '22222222-2222-4222-8222-222222222222',
    timestamp: '2026-08-05T10:15:00+00:00',
    details_json: { event_type: 'response_action_approved', actor: 'ops@example.com' },
  });
  // The captured email is used, never the bare UUID.
  expect(dto.actorEmail).toBe('ops@example.com');
});

// The legacy fallback MUST branch on event kind: a pre-DTO NON-approval audit row (e.g.
// a created / executed event) must NEVER be misrepresented as "Response Action Approved"
// with a "Success" result — that would corrupt the audit stream during a rollout.
test('legacy fallback does not render a non-approval event as an approval', () => {
  const created = normalizeHistoryEventDto({
    id: 'legacy-created-1',
    action_type: 'response_action.created',
    object_type: 'response_action',
    timestamp: '2026-08-05T09:00:00+00:00',
    details_json: {}, // pre-DTO: no event_type / display_label
  });
  expect(created.eventLabel).toBe('Response Action Created');
  expect(created.eventLabel).not.toBe('Response Action Approved');
  expect(created.typeLabel).not.toBe('Action Approval');
  expect(created.decision).toBeNull();
  expect(created.result).not.toBe('Success');

  const executed = normalizeHistoryEventDto({
    id: 'legacy-exec-1',
    action_type: 'response_action.executed',
    object_type: 'response_action',
    timestamp: '2026-08-05T09:05:00+00:00',
    details_json: {},
  });
  expect(executed.eventLabel).toBe('Response Action Executed');
  expect(executed.eventLabel).not.toBe('Response Action Approved');
  expect(executed.decision).toBeNull();

  // An approval-domain legacy row still maps to the approval presentation.
  const approved = normalizeHistoryEventDto({
    id: 'legacy-approve-1',
    action_type: 'response_action.approved',
    object_type: 'response_action',
    timestamp: '2026-08-05T09:10:00+00:00',
    details_json: {},
  });
  expect(approved.eventLabel).toBe('Response Action Approved');
  expect(approved.typeLabel).toBe('Action Approval');
});
