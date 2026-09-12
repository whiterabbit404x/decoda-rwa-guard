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
import {
  FEEDBACK_MODE_LABELS,
  FEEDBACK_SEVERITY_OPTIONS,
  FEEDBACK_TYPE_OPTIONS,
  PRODUCTION_BLOCKER_OPTIONS,
  feedbackTypeLabel,
  productionBlockerLabel,
} from 'app/plan-feedback';
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
  organization_plan: string | null;
  user_email: string | null;
  feedback_type: string;
  message: string;
  created_at: string | null;
  context: Record<string, string> | null;
  /**
   * Null means "this deployment cannot record that yet" (the API is ahead of
   * migration 0153), NOT "the customer left it blank". The two render
   * differently — see `detailCell`.
   */
  detail_available: boolean;
  feedback_mode: string | null;
  severity: string | null;
  production_blocker: string | null;
  continue_intent: string | null;
  contact_permission: boolean | null;
  pilot_day: number | null;
  goal_or_task: string | null;
  security_problem: string | null;
  current_workaround: string | null;
  where_decoda_helped: string | null;
  missing_or_difficult: string | null;
  deployment_requirement: string | null;
  paid_capability: string | null;
};

type FeedbackSummary = {
  total: number;
  production_blockers: number;
  high_or_critical: number;
  missing_capability: number;
  detection_issues: number;
};

type FeedbackFilters = {
  feedback_type: string;
  severity: string;
  production_blocker: string;
  organization_id: string;
  since: string;
};

const EMPTY_FEEDBACK_FILTERS: FeedbackFilters = {
  feedback_type: '',
  severity: '',
  production_blocker: '',
  organization_id: '',
  since: '',
};

/**
 * One stored answer, or an honest reason there is none.
 *
 * A blank cell would read the same whether the customer skipped the question or
 * the deployment cannot store the answer at all. Those are different facts, so
 * they get different words.
 */
function detailCell(value: string | null, detailAvailable: boolean): string {
  if (!detailAvailable) {
    return 'Not recorded on this deployment';
  }
  return value && value.trim().length > 0 ? value : 'Not answered';
}

/** A short preview of a long answer for the summary table. Never mid-word. */
function summarize(message: string, limit = 160): string {
  const text = (message ?? '').trim();
  if (text.length <= limit) {
    return text;
  }
  return `${text.slice(0, text.lastIndexOf(' ', limit) > 0 ? text.lastIndexOf(' ', limit) : limit)}…`;
}

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
  const [feedbackSummary, setFeedbackSummary] = useState<FeedbackSummary | null>(null);
  const [feedbackDetailAvailable, setFeedbackDetailAvailable] = useState(true);
  const [feedbackFilters, setFeedbackFilters] = useState<FeedbackFilters>(EMPTY_FEEDBACK_FILTERS);
  const [openFeedbackId, setOpenFeedbackId] = useState<string | null>(null);
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

      // Filtering happens in SQL, not in this component: the list is capped
      // server-side, so narrowing it here would filter one page and report the
      // counters of another.
      const feedbackQuery = new URLSearchParams();
      for (const [key, value] of Object.entries(feedbackFilters)) {
        if (value) {
          feedbackQuery.set(key, value);
        }
      }
      const feedbackUrl = feedbackQuery.size > 0
        ? `/api/admin/feedback?${feedbackQuery.toString()}`
        : '/api/admin/feedback';
      const feedbackResponse = await fetch(feedbackUrl, { headers: authHeaders(), cache: 'no-store' });
      if (feedbackResponse.ok) {
        const feedbackPayload = (await feedbackResponse.json()) as {
          feedback?: FeedbackItem[];
          summary?: FeedbackSummary;
          detail_available?: boolean;
        };
        setFeedback(feedbackPayload.feedback ?? []);
        setFeedbackSummary(feedbackPayload.summary ?? null);
        setFeedbackDetailAvailable(feedbackPayload.detail_available !== false);
      }
    } catch {
      setError('Customers could not be loaded.');
    } finally {
      setLoading(false);
    }
  }, [authHeaders, feedbackFilters, isAuthenticated]);

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

  const openFeedback = feedback.find((item) => item.id === openFeedbackId) ?? null;

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

      {/* Roadmap signal: four counters over exactly the rows below, computed by
          the backend from the same filtered query, so a counter can never claim
          a pattern the visible list does not contain. */}
      {feedbackDetailAvailable ? null : (
        <p className="statusLine" style={{ color: 'var(--warning-fg)' }}>
          This deployment has not run migration 0153, so severity, production-blocker and the
          detailed answers are not recorded yet. The counters below are not evidence that nobody
          reported them.
        </p>
      )}
      {feedbackSummary ? (
        <div className="adminFeedbackSummary">
          <span data-testid="feedback-summary-blockers">
            Production blockers: <strong>{feedbackDetailAvailable ? feedbackSummary.production_blockers : '—'}</strong>
          </span>
          <span data-testid="feedback-summary-severity">
            High/Critical feedback: <strong>{feedbackDetailAvailable ? feedbackSummary.high_or_critical : '—'}</strong>
          </span>
          <span data-testid="feedback-summary-missing">
            Missing capability: <strong>{feedbackSummary.missing_capability}</strong>
          </span>
          <span data-testid="feedback-summary-detection">
            Detection issues: <strong>{feedbackSummary.detection_issues}</strong>
          </span>
        </div>
      ) : null}

      <div className="adminFeedbackFilters">
        <label>
          Type
          <select
            data-testid="feedback-filter-type"
            value={feedbackFilters.feedback_type}
            onChange={(event) =>
              setFeedbackFilters((prev) => ({ ...prev, feedback_type: event.target.value }))
            }
          >
            <option value="">All</option>
            {FEEDBACK_TYPE_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>{option.label}</option>
            ))}
          </select>
        </label>
        <label>
          Severity
          <select
            data-testid="feedback-filter-severity"
            value={feedbackFilters.severity}
            onChange={(event) => setFeedbackFilters((prev) => ({ ...prev, severity: event.target.value }))}
          >
            <option value="">All</option>
            {FEEDBACK_SEVERITY_OPTIONS.filter((option) => option.value).map((option) => (
              <option key={option.value} value={option.value}>{option.label}</option>
            ))}
          </select>
        </label>
        <label>
          Production blocker
          <select
            data-testid="feedback-filter-blocker"
            value={feedbackFilters.production_blocker}
            onChange={(event) =>
              setFeedbackFilters((prev) => ({ ...prev, production_blocker: event.target.value }))
            }
          >
            <option value="">All</option>
            {PRODUCTION_BLOCKER_OPTIONS.filter((option) => option.value).map((option) => (
              <option key={option.value} value={option.value}>{option.label}</option>
            ))}
          </select>
        </label>
        <label>
          Organization
          <select
            data-testid="feedback-filter-organization"
            value={feedbackFilters.organization_id}
            onChange={(event) =>
              setFeedbackFilters((prev) => ({ ...prev, organization_id: event.target.value }))
            }
          >
            <option value="">All</option>
            {customers.map((customer) => (
              <option key={customer.id} value={customer.id}>
                {customer.name ?? customer.slug ?? customer.id}
              </option>
            ))}
          </select>
        </label>
        <label>
          Since
          <input
            type="date"
            data-testid="feedback-filter-since"
            value={feedbackFilters.since}
            onChange={(event) => setFeedbackFilters((prev) => ({ ...prev, since: event.target.value }))}
          />
        </label>
        <button
          type="button"
          className="btn"
          data-testid="feedback-filter-clear"
          onClick={() => setFeedbackFilters(EMPTY_FEEDBACK_FILTERS)}
        >
          Clear filters
        </button>
      </div>

      {feedback.length === 0 ? (
        <p className="muted">
          {Object.values(feedbackFilters).some(Boolean)
            ? 'No feedback matches these filters.'
            : 'No feedback submitted yet.'}
        </p>
      ) : (
        <div className="adminTableWrap">
          <table className="adminTable">
            <thead>
              <tr>
                <th>Date</th>
                <th>Organization</th>
                <th>Primary contact</th>
                <th>Mode</th>
                <th>Type</th>
                <th>Severity</th>
                <th>Production blocker</th>
                <th style={{ whiteSpace: 'normal' }}>Summary</th>
                <th>Context</th>
                <th>Detail</th>
              </tr>
            </thead>
            <tbody>
              {feedback.map((item) => (
                <tr key={item.id}>
                  <td>{formatDate(item.created_at)}</td>
                  <td>{item.organization_name ?? item.organization_id}</td>
                  <td>
                    <span className="adminContactEmail" title={item.user_email ?? undefined}>
                      {item.user_email ?? 'No contact on record'}
                    </span>
                  </td>
                  <td>{item.feedback_mode ? FEEDBACK_MODE_LABELS[item.feedback_mode] ?? item.feedback_mode : '—'}</td>
                  <td>{feedbackTypeLabel(item.feedback_type)}</td>
                  <td data-testid="feedback-row-severity">
                    {item.severity ? item.severity.toUpperCase() : '—'}
                  </td>
                  <td data-testid="feedback-row-blocker">{productionBlockerLabel(item.production_blocker)}</td>
                  {/* Truncated for the row; the full text is one click away in the
                      detail panel rather than stretching the table off-screen. */}
                  <td style={{ whiteSpace: 'normal', maxWidth: '28rem' }}>{summarize(item.message)}</td>
                  <td>
                    {item.context?.page ?? '—'}
                    {item.context?.incident_id ? (
                      <span className="adminContactMembers">incident {item.context.incident_id}</span>
                    ) : null}
                    {item.context?.alert_id ? (
                      <span className="adminContactMembers">alert {item.context.alert_id}</span>
                    ) : null}
                    {item.context?.asset_id ? (
                      <span className="adminContactMembers">asset {item.context.asset_id}</span>
                    ) : null}
                  </td>
                  <td>
                    <button
                      type="button"
                      className="btn"
                      data-testid={`feedback-open-${item.id}`}
                      onClick={() => setOpenFeedbackId(openFeedbackId === item.id ? null : item.id)}
                    >
                      {openFeedbackId === item.id ? 'Hide' : 'View'}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {openFeedback ? <FeedbackDetail item={openFeedback} onClose={() => setOpenFeedbackId(null)} /> : null}

    </main>
  );
}


/**
 * One feedback row in full — the founder's read of a single customer answer.
 *
 * Internal-only, like everything else on this page: it renders what
 * GET /admin/feedback already returned for an authorized internal admin, and
 * fetches nothing of its own. There is no secret to hide here because the
 * backend refuses a submission that looks like it carries one, so no credential
 * is ever stored to be rendered.
 */
function FeedbackDetail({ item, onClose }: { item: FeedbackItem; onClose: () => void }) {
  const available = item.detail_available;
  const answers: Array<{ label: string; value: string | null }> = [
    { label: 'What were you trying to do?', value: item.goal_or_task },
    { label: 'What problem were you solving?', value: item.security_problem },
    { label: 'How do you handle it today?', value: item.current_workaround },
    { label: 'Where did Decoda help?', value: item.where_decoda_helped },
    { label: 'What was missing?', value: item.missing_or_difficult },
    { label: 'What would Decoda need before production?', value: item.deployment_requirement },
    { label: 'Which capability would you pay for?', value: item.paid_capability },
  ];

  return (
    <section className="adminFeedbackDetail" data-testid="feedback-detail" aria-label="Feedback detail">
      <div className="adminFeedbackDetailHeader">
        <h3 style={{ margin: 0, fontSize: '0.95rem' }}>
          {item.organization_name ?? item.organization_id}
        </h3>
        <button type="button" className="btn" onClick={onClose}>
          Close
        </button>
      </div>

      <div className="adminFeedbackDetailMeta">
        <span>Primary contact: <strong>{item.user_email ?? 'No contact on record'}</strong></span>
        <span>Plan: <strong>{item.organization_plan ?? '—'}</strong></span>
        <span>
          Pilot day: <strong>{item.pilot_day === null ? (available ? 'Not applicable' : '—') : item.pilot_day}</strong>
        </span>
        <span>
          Mode: <strong>{item.feedback_mode ? FEEDBACK_MODE_LABELS[item.feedback_mode] ?? item.feedback_mode : '—'}</strong>
        </span>
        <span>Type: <strong>{feedbackTypeLabel(item.feedback_type)}</strong></span>
        <span>Severity: <strong>{item.severity ? item.severity.toUpperCase() : '—'}</strong></span>
        <span data-testid="feedback-detail-blocker">
          Production blocker: <strong>{productionBlockerLabel(item.production_blocker)}</strong>
        </span>
        {item.continue_intent ? (
          <span>Would continue: <strong>{item.continue_intent}</strong></span>
        ) : null}
        <span data-testid="feedback-detail-contact">
          {/* Truthful default: contact permission is FALSE until the customer
              ticked the box, and an unrecordable column is neither yes nor no. */}
          Contact permitted: <strong>{item.contact_permission === null ? '—' : item.contact_permission ? 'Yes' : 'No'}</strong>
        </span>
        <span>Submitted: <strong>{formatDate(item.created_at)}</strong></span>
      </div>

      <div className="adminFeedbackDetailBody">
        <p className="sectionEyebrow">Summary</p>
        <p className="adminFeedbackAnswer">{item.message}</p>
        {answers.map((answer) => (
          <div key={answer.label}>
            <p className="sectionEyebrow">{answer.label}</p>
            <p className="adminFeedbackAnswer">{detailCell(answer.value, available)}</p>
          </div>
        ))}
        <p className="sectionEyebrow">Context</p>
        <p className="adminFeedbackAnswer">
          {item.context?.page ?? 'No page recorded'}
          {item.context?.incident_id ? ` · incident ${item.context.incident_id}` : ''}
          {item.context?.alert_id ? ` · alert ${item.context.alert_id}` : ''}
          {item.context?.asset_id ? ` · asset ${item.context.asset_id}` : ''}
        </p>
      </div>
    </section>
  );
}
