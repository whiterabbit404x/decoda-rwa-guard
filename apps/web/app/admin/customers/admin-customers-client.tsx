'use client';

import { useCallback, useEffect, useState } from 'react';

import {
  otherMembersLabel,
  primaryContactEmail,
  primaryContactLabel,
} from 'app/admin-customer-contact';
import {
  canApprove,
  canReject,
  canResendInvitation,
  formatDate as formatRequestDate,
  statusLabel,
  type PilotRequest,
} from 'app/admin/pilot-requests-view';
import { mutateWithCsrfRetry } from 'app/csrf-retry';
import { usePilotAuth } from 'app/pilot-auth-context';
import { PLAN_LABELS, usageLabel, type UsageEntry } from 'app/plan-status';

type AdminCustomer = {
  id: string;
  name: string | null;
  slug: string | null;
  plan: 'pilot' | 'scale' | 'enterprise';
  status: 'active' | 'suspended' | 'expired';
  lifecycle_state: string;
  evaluation: { expires_at: string | null; days_remaining: number | null; expired: boolean } | null;
  created_at: string | null;
  last_activity_at: string | null;
  /** Owner, else admin, else earliest member — resolved by the backend. */
  primary_contact_email: string | null;
  members: number;
  feedback_count: number;
  usage: Record<string, UsageEntry>;
};

type FeedbackItem = {
  id: string;
  organization_id: string;
  organization_name: string | null;
  user_email: string | null;
  feedback_type: string;
  message: string;
  created_at: string | null;
};

function formatDate(value: string | null): string {
  if (!value) {
    return '—';
  }
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? '—' : parsed.toISOString().slice(0, 10);
}

function evaluationCell(customer: AdminCustomer): string {
  if (!customer.evaluation || !customer.evaluation.expires_at) {
    // Truthful: an organization with no deadline has none. Not "expired",
    // and not a fabricated date.
    return 'No deadline';
  }
  const date = formatDate(customer.evaluation.expires_at);
  if (customer.evaluation.expired) {
    return `${date} (expired)`;
  }
  const days = customer.evaluation.days_remaining;
  return typeof days === 'number' ? `${date} (${days}d)` : date;
}

const SESSION_EXPIRED_MESSAGE = 'Your session is missing or expired. Please sign in again.';

/** The message shown for a failed mutation, preferring the backend's own words. */
function failureMessage(status: number, payload: Record<string, unknown>): string {
  if (status === 401) {
    return SESSION_EXPIRED_MESSAGE;
  }
  const detail = payload.detail;
  if (detail && typeof detail === 'object' && typeof (detail as Record<string, unknown>).message === 'string') {
    return String((detail as Record<string, unknown>).message);
  }
  return `Action failed (HTTP ${status}).`;
}

export default function AdminCustomersClient() {
  const { authHeaders, csrfReady, isAuthenticated, refreshCsrfToken } = usePilotAuth();
  const [customers, setCustomers] = useState<AdminCustomer[]>([]);
  const [pilotRequests, setPilotRequests] = useState<PilotRequest[]>([]);
  const [feedback, setFeedback] = useState<FeedbackItem[]>([]);
  const [denied, setDenied] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!isAuthenticated) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const response = await fetch('/api/admin/customers', { headers: authHeaders(), cache: 'no-store' });
      if (response.status === 403) {
        setDenied(true);
        setCustomers([]);
        return;
      }
      if (!response.ok) {
        setError(`Customers could not be loaded (HTTP ${response.status}).`);
        return;
      }
      setDenied(false);
      const payload = (await response.json()) as { customers?: AdminCustomer[] };
      setCustomers(payload.customers ?? []);

      // The review queue. It is loaded from the same internal-admin session and
      // is never reachable by a customer: the backend authorizes before reading.
      const requestsResponse = await fetch('/api/admin/pilot-requests', {
        headers: authHeaders(),
        cache: 'no-store',
      });
      if (requestsResponse.ok) {
        const requestsPayload = (await requestsResponse.json()) as { requests?: PilotRequest[] };
        setPilotRequests(requestsPayload.requests ?? []);
      }

      const feedbackResponse = await fetch('/api/admin/feedback', { headers: authHeaders(), cache: 'no-store' });
      if (feedbackResponse.ok) {
        const feedbackPayload = (await feedbackResponse.json()) as { feedback?: FeedbackItem[] };
        setFeedback(feedbackPayload.feedback ?? []);
      }
    } catch {
      setError('Customers could not be loaded.');
    } finally {
      setLoading(false);
    }
  }, [authHeaders, isAuthenticated]);

  useEffect(() => {
    void load();
  }, [load]);

  // The provider mints the anti-CSRF token once, at sign-in and at session
  // restore. If that single attempt failed (a backend blip), every action button
  // below stays disabled on `!csrfReady` until the founder reloads the page —
  // the other half of the "a refresh fixes it" report. Minting one here when the
  // console opens without a token closes that hole. Nothing is re-minted when a
  // token already exists, and a token that dies LATER is handled by the
  // per-mutation retry rather than here.
  useEffect(() => {
    if (!isAuthenticated || csrfReady) {
      return;
    }
    void refreshCsrfToken().catch(() => undefined);
  }, [csrfReady, isAuthenticated, refreshCsrfToken]);

  // Extend 30d / Suspend / Reactivate / Upgrade to Scale. Routed through
  // mutateWithCsrfRetry so an anti-CSRF token that expired while this tab was
  // open heals itself instead of surfacing a 403 the founder has to fix with a
  // browser refresh. A non-CSRF 403 (INTERNAL_ADMIN_REQUIRED) is NOT retried and
  // is reported exactly as the backend stated it.
  async function act(organizationId: string, path: string, body: Record<string, unknown>) {
    setBusyId(organizationId);
    setError(null);
    try {
      const { response, payload } = await mutateWithCsrfRetry({
        url: `/api/admin/customers/${organizationId}/${path}`,
        body,
        authHeaders,
        refreshCsrfToken,
      });
      if (!response.ok) {
        setError(failureMessage(response.status, payload));
        return;
      }
      await load();
    } catch {
      setError('Action failed.');
    } finally {
      setBusyId(null);
    }
  }

  // Approve / Reject / Resend invitation. Same self-healing anti-CSRF path as
  // act(): the CSRF middleware refuses BEFORE the route handler runs, so a
  // rejected attempt minted no invitation and sent no email, and replaying it
  // once with a current token cannot duplicate either.
  async function actOnRequest(requestId: string, path: string, body: Record<string, unknown> = {}) {
    setBusyId(requestId);
    setError(null);
    try {
      const { response, payload } = await mutateWithCsrfRetry({
        url: `/api/admin/pilot-requests/${requestId}/${path}`,
        body,
        authHeaders,
        refreshCsrfToken,
      });
      if (!response.ok) {
        setError(failureMessage(response.status, payload));
        return;
      }
      // An approval whose email failed is reported here rather than being
      // rendered as a clean success: the founder needs to know the applicant
      // received nothing.
      if (payload.invitation_sent === false) {
        setError(
          'Approved, but the invitation email did not send. Use "Resend invitation" once email delivery is working.',
        );
      }
      await load();
    } catch {
      setError('Action failed.');
    } finally {
      setBusyId(null);
    }
  }

  if (denied) {
    return (
      <main className="adminConsole">
        <div className="adminConsoleDenied">
          <h1 style={{ fontSize: '1.05rem', margin: '0 0 0.4rem' }}>Not available</h1>
          <p style={{ margin: 0, fontSize: '0.85rem' }}>
            This area is restricted to Decoda internal staff.
          </p>
        </div>
      </main>
    );
  }

  return (
    <main className="adminConsole">
      <p className="sectionEyebrow">Decoda internal</p>
      <h1 style={{ fontSize: '1.3rem', margin: '0 0 1rem' }}>Pilot requests &amp; customers</h1>

      {error ? <p className="statusLine" style={{ color: 'var(--danger-fg)' }}>{error}</p> : null}
      {loading ? <p className="muted">Loading…</p> : null}

      <h2 style={{ fontSize: '1.05rem', margin: '0 0 0.6rem' }}>Pilot requests</h2>
      <p className="muted" style={{ margin: '0 0 0.6rem', fontSize: '0.82rem' }}>
        Approving sends a single-use invitation to the applicant. It does not activate anything:
        the Pilot organization is created when the approved person accepts.
      </p>
      {!loading && pilotRequests.length === 0 ? (
        <p className="muted">No Pilot requests yet.</p>
      ) : (
        <div className="adminTableWrap">
          <table className="adminTable">
            <thead>
              <tr>
                <th>Company</th>
                <th>Primary contact</th>
                <th>Role</th>
                <th>Requested</th>
                <th>Status</th>
                <th>Use case</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {pilotRequests.map((request) => (
                <tr key={request.id}>
                  <td>
                    {request.company_name ?? '—'}
                    {request.company_website ? (
                      <span className="adminContactMembers">{request.company_website}</span>
                    ) : null}
                  </td>
                  <td>
                    <span className="adminContactEmail" title={request.email}>{request.email}</span>
                  </td>
                  <td>{request.role ?? '—'}</td>
                  <td>{formatRequestDate(request.requested_at)}</td>
                  <td>
                    {statusLabel(request)}
                    {request.invitation_delivery_error ? (
                      <span className="adminContactMembers">Email delivery failed</span>
                    ) : null}
                  </td>
                  <td style={{ whiteSpace: 'normal', maxWidth: '22rem' }}>{request.use_case ?? '—'}</td>
                  <td>
                    <div className="adminRowActions">
                      {canApprove(request) ? (
                        <button
                          type="button"
                          className="btn"
                          disabled={!csrfReady || busyId === request.id}
                          onClick={() => void actOnRequest(request.id, 'approve')}
                        >
                          Approve
                        </button>
                      ) : null}
                      {canResendInvitation(request) ? (
                        <button
                          type="button"
                          className="btn"
                          disabled={!csrfReady || busyId === request.id}
                          onClick={() => void actOnRequest(request.id, 'resend-invitation')}
                        >
                          Resend invitation
                        </button>
                      ) : null}
                      {canReject(request) ? (
                        <button
                          type="button"
                          className="btn"
                          disabled={!csrfReady || busyId === request.id}
                          onClick={() => void actOnRequest(request.id, 'reject')}
                        >
                          Reject
                        </button>
                      ) : null}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <h2 style={{ fontSize: '1.05rem', margin: '2rem 0 0.6rem' }}>Customer organizations</h2>

      {!loading && customers.length === 0 ? (
        <p className="muted">No organizations yet.</p>
      ) : (
        <div className="adminTableWrap">
          <table className="adminTable">
            <thead>
              <tr>
                <th>Organization</th>
                <th>Primary contact</th>
                <th>Plan</th>
                <th>Status</th>
                <th>Evaluation expires</th>
                <th>Workspaces</th>
                <th>Contracts</th>
                <th>Evidence</th>
                <th>Last activity</th>
                <th>Feedback</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {customers.map((customer) => (
                <tr key={customer.id}>
                  <td>{customer.name ?? customer.slug ?? customer.id}</td>
                  <td>
                    {/* Truncated for layout only; the full address stays reachable
                        as the title tooltip so a long one is never silently cut. */}
                    <span
                      className="adminContactEmail"
                      title={primaryContactEmail(customer) ?? undefined}
                    >
                      {primaryContactLabel(customer)}
                    </span>
                    {otherMembersLabel(customer) ? (
                      <span className="adminContactMembers">{otherMembersLabel(customer)}</span>
                    ) : null}
                  </td>
                  <td>{PLAN_LABELS[customer.plan] ?? customer.plan}</td>
                  <td>{customer.status}</td>
                  <td>{evaluationCell(customer)}</td>
                  <td>{usageLabel(customer.usage?.workspaces)}</td>
                  <td>{usageLabel(customer.usage?.monitored_contracts)}</td>
                  <td>{usageLabel(customer.usage?.evidence_packages)}</td>
                  <td>{formatDate(customer.last_activity_at)}</td>
                  <td>{customer.feedback_count}</td>
                  <td>
                    <div className="adminRowActions">
                      <button
                        type="button"
                        className="btn"
                        disabled={!csrfReady || busyId === customer.id}
                        onClick={() => void act(customer.id, 'extend-evaluation', { days: 30 })}
                      >
                        Extend 30d
                      </button>
                      {customer.status === 'suspended' ? (
                        <button
                          type="button"
                          className="btn"
                          disabled={!csrfReady || busyId === customer.id}
                          onClick={() => void act(customer.id, 'status', { status: 'active' })}
                        >
                          Reactivate
                        </button>
                      ) : (
                        <button
                          type="button"
                          className="btn"
                          disabled={!csrfReady || busyId === customer.id}
                          onClick={() => void act(customer.id, 'status', { status: 'suspended' })}
                        >
                          Suspend
                        </button>
                      )}
                      {customer.plan === 'pilot' ? (
                        <button
                          type="button"
                          className="btn"
                          disabled={!csrfReady || busyId === customer.id}
                          onClick={() => void act(customer.id, 'plan', { plan: 'scale' })}
                        >
                          Upgrade to Scale
                        </button>
                      ) : null}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <h2 style={{ fontSize: '1.05rem', margin: '2rem 0 0.6rem' }}>Evaluator feedback</h2>
      {feedback.length === 0 ? (
        <p className="muted">No feedback submitted yet.</p>
      ) : (
        <div className="adminTableWrap">
          <table className="adminTable">
            <thead>
              <tr>
                <th>Date</th>
                <th>Organization</th>
                <th>Type</th>
                <th>From</th>
                <th style={{ whiteSpace: 'normal' }}>Message</th>
              </tr>
            </thead>
            <tbody>
              {feedback.map((item) => (
                <tr key={item.id}>
                  <td>{formatDate(item.created_at)}</td>
                  <td>{item.organization_name ?? item.organization_id}</td>
                  <td>{item.feedback_type}</td>
                  <td>{item.user_email ?? '—'}</td>
                  <td style={{ whiteSpace: 'normal', maxWidth: '32rem' }}>{item.message}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </main>
  );
}
