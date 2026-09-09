// ─────────────────────────────────────────────────────────────
// Presentation logic for the internal Pilot Requests table.
//
// Split out of the client component so the truthfulness rules it encodes are
// testable without a browser:
//
//   * an approval whose email did NOT send is labelled "Approved — invitation
//     not sent", never "Invited". Telling the founder a link went out when it
//     did not is exactly the kind of quiet falsehood this product refuses.
//   * an invitation whose window has closed reads "Invitation expired", not
//     "Invited", even before any sweep has rewritten the row.
//   * a row is actionable only in the states where the backend would actually
//     accept the action; the button set never implies a power the API refuses.
// ─────────────────────────────────────────────────────────────

export type PilotRequestStatus =
  | 'pending'
  | 'approved'
  | 'invited'
  | 'rejected'
  | 'activated'
  | 'expired';

export type PilotRequest = {
  id: string;
  email: string;
  company_name: string | null;
  role: string | null;
  company_website: string | null;
  use_case: string | null;
  status: PilotRequestStatus;
  requested_at: string | null;
  reviewed_at: string | null;
  approved_at: string | null;
  rejected_at: string | null;
  internal_note: string | null;
  invitation_expires_at: string | null;
  invitation_sent_at: string | null;
  invitation_accepted_at: string | null;
  invitation_delivery_error: string | null;
  invitation_expired: boolean;
  invitation_not_sent: boolean;
  organization_id: string | null;
};

/** The label the console shows. Derived, never a bare echo of `status`. */
export function statusLabel(request: PilotRequest): string {
  if (request.status === 'approved' && request.invitation_expired) {
    return 'Invitation expired';
  }
  if (request.status === 'invited' && request.invitation_expired) {
    return 'Invitation expired';
  }
  if (request.status === 'approved' && request.invitation_not_sent) {
    return 'Approved — invitation not sent';
  }
  switch (request.status) {
    case 'pending':
      return 'Pending review';
    case 'approved':
      return 'Approved — invitation sent';
    case 'invited':
      return 'Invitation sent';
    case 'rejected':
      return 'Rejected';
    case 'activated':
      return 'Pilot activated';
    case 'expired':
      return 'Invitation expired';
    default:
      return request.status;
  }
}

/** Approve is offered wherever the backend would accept it. */
export function canApprove(request: PilotRequest): boolean {
  return request.status === 'pending' || request.status === 'expired';
}

/** Reject is offered until the request has actually created a tenant. */
export function canReject(request: PilotRequest): boolean {
  return request.status !== 'activated' && request.status !== 'rejected';
}

/**
 * A retry is offered only where an invitation exists but has not been used:
 * after a delivery failure, or once the window has closed. It always mints a
 * fresh token, so it is never a way to re-send a link that may have leaked.
 */
export function canResendInvitation(request: PilotRequest): boolean {
  if (request.status === 'activated' || request.status === 'rejected') {
    return false;
  }
  return request.status === 'approved' || request.status === 'invited' || request.status === 'expired';
}

export function formatDate(value: string | null): string {
  if (!value) {
    return '—';
  }
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? '—' : parsed.toISOString().slice(0, 10);
}
