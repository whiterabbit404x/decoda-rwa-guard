import { expect, test } from '@playwright/test';

import {
  canonicalApprovalStatus,
  canonicalLifecycleState,
  deriveRowApproval,
  humanizeLabel,
  lifecycleLabelFor,
  reconcileCount,
} from '../app/(product)/response-actions-presentation';

// Behavioral (not source-string) tests for the ONE canonical Screen 8 presentation
// module. These pin the fix for the deployed contradiction: "Pending Approval: 0"
// while every row shows "Requires Approval", and a raw `Pending_approval` enum
// reaching the UI. The invariants are enforced here so they cannot silently
// regress across a stale/partial backend payload.

// ── canonicalApprovalStatus: legacy variants collapse onto the canonical set ──

test('canonicalApprovalStatus normalizes every legacy pending variant to "pending"', () => {
  for (const raw of ['pending', 'pending_approval', 'PENDING_APPROVAL', 'Pending_Approval',
    'awaiting_approval', 'pending_review', 'requires_approval', 'needs_approval']) {
    expect(canonicalApprovalStatus(raw)).toBe('pending');
  }
  expect(canonicalApprovalStatus('approved')).toBe('approved');
  expect(canonicalApprovalStatus('accepted')).toBe('approved');
  expect(canonicalApprovalStatus('rejected')).toBe('rejected');
  expect(canonicalApprovalStatus('not_required')).toBe('not_required');
  expect(canonicalApprovalStatus('')).toBe('');
  expect(canonicalApprovalStatus(undefined)).toBe('');
});

// ── deriveRowApproval: pill and count derive from ONE value (can't contradict) ─

test('a canonical pending approval_status is required AND pending', () => {
  const r = deriveRowApproval({ approvalStatus: 'pending' });
  expect(r).toEqual({ approvalStatus: 'pending', requiresApproval: true });
});

test('a canonical not_required approval_status is NOT required (notify_team)', () => {
  const r = deriveRowApproval({ approvalStatus: 'not_required', rawStatus: 'pending' });
  expect(r).toEqual({ approvalStatus: 'not_required', requiresApproval: false });
});

test('approved/rejected still require approval but are not pending', () => {
  expect(deriveRowApproval({ approvalStatus: 'approved' })).toEqual({
    approvalStatus: 'approved',
    requiresApproval: true,
  });
  expect(deriveRowApproval({ approvalStatus: 'rejected' })).toEqual({
    approvalStatus: 'rejected',
    requiresApproval: true,
  });
});

test('legacy payload: an explicit approval-pending raw status counts as pending', () => {
  // No canonical approval_status (stale backend), but the raw status is the
  // approval-specific `pending_approval` enum -> required AND pending, so the row is
  // never shown as "Requires Approval" while excluded from the count.
  const r = deriveRowApproval({ rawStatus: 'pending_approval' });
  expect(r).toEqual({ approvalStatus: 'pending', requiresApproval: true });
});

test('legacy payload: a generic policy status "pending" alone does NOT imply approval', () => {
  // A policy action `status = 'pending'` only means "not yet executed". With no
  // canonical approval_status and no explicit requires_approval, fail CLOSED to
  // not-required so a notify_team row does not falsely read "Requires Approval".
  const r = deriveRowApproval({ rawStatus: 'pending' });
  expect(r).toEqual({ approvalStatus: 'not_required', requiresApproval: false });
});

test('legacy payload: an explicit requires_approval=true counts as pending', () => {
  const r = deriveRowApproval({ requiresApproval: true, rawStatus: 'pending' });
  expect(r).toEqual({ approvalStatus: 'pending', requiresApproval: true });
});

test('INVARIANT: requiresApproval implies a non-not_required status (no contradiction)', () => {
  const inputs = [
    { approvalStatus: 'pending' },
    { approvalStatus: 'approved' },
    { approvalStatus: 'not_required' },
    { rawStatus: 'pending_approval' },
    { rawStatus: 'pending' },
    { requiresApproval: true },
    {},
  ];
  for (const input of inputs) {
    const { approvalStatus, requiresApproval } = deriveRowApproval(input);
    // A row shown as "Requires Approval" is never simultaneously "not_required".
    expect(requiresApproval).toBe(approvalStatus !== 'not_required');
  }
});

// ── lifecycle labels: no raw enum ever reaches the UI ─────────────────────────

test('a raw pending_approval enum renders as "Awaiting Approval", never "Pending_approval"', () => {
  // Even when the backend label itself is a raw snake_case enum, it is rejected.
  expect(lifecycleLabelFor('pending_approval', 'pending_approval')).toBe('Awaiting Approval');
  expect(lifecycleLabelFor('pending_approval')).toBe('Awaiting Approval');
  expect(lifecycleLabelFor('awaiting_approval')).toBe('Awaiting Approval');
});

test('lifecycleLabelFor trusts a clean backend label but never emits underscores', () => {
  expect(lifecycleLabelFor('simulation_passed', 'Simulation Passed')).toBe('Simulation Passed');
  // Unknown state with no clean backend label -> humanized, no underscores survive.
  const label = lifecycleLabelFor('some_new_adapter_state');
  expect(label).toBe('Some New Adapter State');
  expect(label).not.toContain('_');
});

test('humanizeLabel strips underscores into spaced Title Case', () => {
  expect(humanizeLabel('pending_approval')).toBe('Pending Approval');
  expect(humanizeLabel('execution_failed')).toBe('Execution Failed');
});

test('canonicalLifecycleState maps legacy variants onto the canonical state', () => {
  expect(canonicalLifecycleState('pending_approval')).toBe('awaiting_approval');
  expect(canonicalLifecycleState('pending_review')).toBe('awaiting_approval');
  expect(canonicalLifecycleState('canceled')).toBe('cancelled');
  expect(canonicalLifecycleState('simulation_passed')).toBe('simulation_passed');
  expect(canonicalLifecycleState('')).toBe('recommended');
});

// ── reconcileCount: fail CLOSED to the visible rows on disagreement ───────────

test('reconcileCount prefers the backend value only when it matches the rows', () => {
  expect(reconcileCount(2, 2)).toBe(2); // agree -> canonical
  expect(reconcileCount(0, 2)).toBe(2); // stale backend says 0, rows say 2 -> rows win
  expect(reconcileCount(5, 2)).toBe(2); // backend over-reports -> visible rows win
  expect(reconcileCount(undefined, 2)).toBe(2); // no backend summary -> rows
  expect(reconcileCount(null, 3)).toBe(3);
});

// ── The exact deployed scenario, reduced to the presentation layer ────────────

test('deployed six-action scenario is internally consistent at the presentation layer', () => {
  // 4 simulated notify_team policy actions (not_required) + 2 pending AI reviews.
  const rows = [
    ...Array.from({ length: 4 }, () =>
      deriveRowApproval({ approvalStatus: 'not_required', rawStatus: 'pending' }),
    ),
    ...Array.from({ length: 2 }, () => deriveRowApproval({ approvalStatus: 'pending' })),
  ];
  const rowPending = rows.filter((r) => r.approvalStatus === 'pending').length;
  const rowRequiresApproval = rows.filter((r) => r.requiresApproval).length;
  expect(rowPending).toBe(2);
  // The number of rows showing "Requires Approval" equals the pending count — the
  // contradiction (all 6 "Requires Approval" while Pending Approval = 0) is gone.
  expect(rowRequiresApproval).toBe(2);
  // A stale backend summary that says 0 is corrected to the visible truth.
  expect(reconcileCount(0, rowPending)).toBe(2);
});

test('STALE-backend payloads (the deployed failure mode) render with no contradiction, no raw enum', () => {
  // The exact conditions that produced the deployed contradiction: the payload
  // predates the canonical fields, so there is NO lifecycle/approval_status. Model
  // each row as the frontend now derives it and assert the row is self-consistent
  // AND never shows a raw enum label.
  type LegacyPayload = {
    approval_status?: unknown;
    lifecycle_state?: unknown;
    lifecycle_label?: unknown;
    status: string;
    requires_approval?: unknown;
  };
  const legacyPayloads: LegacyPayload[] = [
    // 4 notify_team policy actions: only a generic status, no canonical fields.
    ...Array.from({ length: 4 }, () => ({ status: 'pending' as const })),
    // 2 AI reviews: legacy top-level status is the raw approval enum, nothing else.
    ...Array.from({ length: 2 }, () => ({ status: 'pending_approval' as const })),
  ];

  const derivedRows = legacyPayloads.map((p) => {
    const { approvalStatus, requiresApproval } = deriveRowApproval({
      approvalStatus: p.approval_status,
      rawStatus: p.status,
      requiresApproval: p.requires_approval,
    });
    const label = lifecycleLabelFor(p.lifecycle_state ?? p.status, p.lifecycle_label);
    return { approvalStatus, requiresApproval, label };
  });

  for (const row of derivedRows) {
    // Invariant: "Requires Approval" pill <=> counted (never contradictory).
    expect(row.requiresApproval).toBe(row.approvalStatus !== 'not_required');
    // No raw snake_case enum ever reaches the UI.
    expect(row.label).not.toContain('_');
  }

  const pending = derivedRows.filter((r) => r.approvalStatus === 'pending').length;
  const requiresApproval = derivedRows.filter((r) => r.requiresApproval).length;
  // Only the 2 AI reviews (raw `pending_approval`) are pending; the 4 generic
  // `pending` notify rows fail closed to not-required. Card == rows == 2.
  expect(pending).toBe(2);
  expect(requiresApproval).toBe(2);
  // The AI-review rows render "Awaiting Approval", never the raw "Pending_approval".
  expect(derivedRows[4].label).toBe('Awaiting Approval');
  expect(reconcileCount(0, pending)).toBe(2);
});
