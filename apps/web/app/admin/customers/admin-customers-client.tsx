'use client';

import { useCallback, useEffect, useState } from 'react';

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

export default function AdminCustomersClient() {
  const { authHeaders, csrfReady, isAuthenticated } = usePilotAuth();
  const [customers, setCustomers] = useState<AdminCustomer[]>([]);
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

  async function act(organizationId: string, path: string, body: Record<string, unknown>) {
    setBusyId(organizationId);
    setError(null);
    try {
      const response = await fetch(`/api/admin/customers/${organizationId}/${path}`, {
        method: 'POST',
        headers: { ...authHeaders(), 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!response.ok) {
        const payload = (await response.json().catch(() => ({}))) as Record<string, unknown>;
        const detail = payload.detail;
        setError(
          detail && typeof detail === 'object' && typeof (detail as Record<string, unknown>).message === 'string'
            ? String((detail as Record<string, unknown>).message)
            : `Action failed (HTTP ${response.status}).`,
        );
        return;
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
      <h1 style={{ fontSize: '1.3rem', margin: '0 0 1rem' }}>Customer organizations</h1>

      {error ? <p className="statusLine" style={{ color: 'var(--danger-fg)' }}>{error}</p> : null}
      {loading ? <p className="muted">Loading…</p> : null}

      {!loading && customers.length === 0 ? (
        <p className="muted">No organizations yet.</p>
      ) : (
        <div className="adminTableWrap">
          <table className="adminTable">
            <thead>
              <tr>
                <th>Organization</th>
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
