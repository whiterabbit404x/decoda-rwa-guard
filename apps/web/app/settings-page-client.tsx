'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';
import type { ReactNode } from 'react';

import { usePilotAuth } from 'app/pilot-auth-context';
import { BillingRuntime, billingDisabledMessage, billingEnabled, billingProviderLabel } from './billing-capability';
import { GovernanceDialog } from './components/governance-dialog';
import {
  Approval,
  ChangeLogItem,
  GovernanceAnomaly,
  GovernanceState,
  LoadState,
  accessRiskLabel,
  anomalyStateLabel,
  changeSafeguardLabel,
  emptyGovernanceState,
  fetchGovernanceState,
  loadStateFor,
  posturePercentLabel,
  riskPillVariant,
} from './governance-view-model';

type TabKey = 'general' | 'team' | 'security' | 'billing' | 'notifications';

const TABS: Array<{ key: TabKey; label: string }> = [
  { key: 'general', label: 'General' },
  { key: 'team', label: 'Team' },
  { key: 'security', label: 'Security' },
  { key: 'billing', label: 'Billing' },
  { key: 'notifications', label: 'Notifications' },
];

const TEAM_MEMBER_HEADERS = ['Member', 'Email', 'Role', 'Status', 'Last Active', 'Actions'] as const;
const CHANNEL_HEADERS = ['Channel', 'Type', 'Status', 'Last Sent', 'Actions'] as const;

type Member = {
  id: string;
  user_id: string;
  email: string;
  full_name: string;
  role: 'owner' | 'admin' | 'analyst' | 'viewer' | 'billing' | 'unknown';
  created_at: string;
};

type Invitation = {
  id: string;
  email: string;
  role: 'owner' | 'admin' | 'analyst' | 'viewer' | 'billing' | 'unknown';
  status: string;
  expires_at: string;
  created_at: string;
  updated_at: string;
};

type SeatSummary = { used: number; limit: number; plan_key?: string };
type ReadinessCheck = { key: string; label: string; pass: boolean; blocking: boolean; reason?: string };
type WorkspaceReadiness = { status: 'pass' | 'fail'; blocking_failures: string[]; checks: ReadinessCheck[]; checked_at: string };

function normaliseStatus(value?: string | null): string {
  return String(value ?? '').trim().toLowerCase();
}

function pillClass(status: string): string {
  const lower = normaliseStatus(status);
  if (['enabled', 'active', 'pass', 'required', 'configured', 'connected', 'healthy'].some((s) => lower.includes(s))) {
    return 'pill pill-success';
  }
  if (['warning', 'pending', 'invited', 'trial', 'expiring', 'optional'].some((s) => lower.includes(s))) {
    return 'pill pill-warning';
  }
  if (['disabled', 'failed', 'error', 'danger', 'past due', 'suspended', 'revoked', 'canceled'].some((s) => lower.includes(s))) {
    return 'pill pill-danger';
  }
  return 'pill pill-neutral';
}

function StatusPill({ status }: { status: string }) {
  return <span className={pillClass(status)}>{status}</span>;
}

function SectionCard({ title, children, action }: { title: string; children: ReactNode; action?: ReactNode }) {
  return (
    <article className="dataCard" style={{ marginBottom: '1rem' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.85rem' }}>
        <p className="sectionEyebrow" style={{ margin: 0 }}>{title}</p>
        {action ?? null}
      </div>
      {children}
    </article>
  );
}

function FieldRow({ label, value, readOnly, note }: { label: string; value: ReactNode; readOnly?: boolean; note?: string }) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1.4fr', gap: '0.5rem 1rem', alignItems: 'center', padding: '0.55rem 0', borderBottom: '1px solid #21262d' }}>
      <span style={{ color: '#8b949e', fontSize: '0.82rem', fontWeight: 600 }}>
        {label}
        {readOnly ? <span style={{ marginLeft: '0.4rem', fontSize: '0.72rem', color: '#5a6478' }}>(read-only)</span> : null}
      </span>
      <span style={{ fontSize: '0.85rem' }}>
        {value}
        {note ? <span style={{ display: 'block', color: '#5a6478', fontSize: '0.75rem', marginTop: '0.15rem' }}>{note}</span> : null}
      </span>
    </div>
  );
}

function MetricCard({ label, value, sub }: { label: string; value: ReactNode; sub?: ReactNode }) {
  return (
    <article className="dataCard" style={{ textAlign: 'center' }}>
      <p className="sectionEyebrow" style={{ marginBottom: '0.35rem' }}>{label}</p>
      <p style={{ margin: '0 0 0.2rem', fontSize: '1.5rem', fontWeight: 700 }}>{value}</p>
      {sub ? <p className="muted" style={{ margin: 0, fontSize: '0.78rem' }}>{sub}</p> : null}
    </article>
  );
}

function EmptyState({ title, message, action, disabled }: { title: string; message: string; action?: string; disabled?: boolean }) {
  return (
    <div style={{ padding: '2rem 1.25rem', textAlign: 'center' }}>
      <h3 style={{ marginTop: 0, fontSize: '1rem' }}>{title}</h3>
      <p className="muted" style={{ marginBottom: '1rem' }}>{message}</p>
      {action ? (
        <button className="btn btn-secondary" type="button" disabled={disabled}>{action}</button>
      ) : null}
    </div>
  );
}

function DataTable({ headers, children }: { headers: readonly string[]; children: ReactNode }) {
  return (
    <div style={{ overflowX: 'auto' }}>
      <table>
        <thead>
          <tr>{headers.map((h) => <th key={h}>{h}</th>)}</tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

function formatDate(value?: string | null): string {
  if (!value) return '-';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return '-';
  return d.toLocaleDateString();
}

function maskId(value?: string | null): string {
  if (!value) return '-';
  if (value.length <= 8) return value;
  return value.slice(0, 4) + '****' + value.slice(-4);
}

export default function SettingsPageClient() {
  // NOTE: customer-facing reads/writes go through the SAME-ORIGIN Next proxy (/api/*),
  // never the browser-exposed apiUrl. The proxy resolves the backend base URL server-side
  // (API_URL) — the same canonical transport /auth/me uses — so these calls do not depend
  // on NEXT_PUBLIC_API_URL being set or on the backend being reachable/CORS-open from the
  // browser. See app/api/_shared/backend-proxy.ts.
  const { authHeaders, csrfReady, error, liveModeConfigured, loading, refreshCsrfToken, user } = usePilotAuth();
  const [members, setMembers] = useState<Member[]>([]);
  const [invitations, setInvitations] = useState<Invitation[]>([]);
  const [plans, setPlans] = useState<Array<{ plan_key: string; plan_name: string; max_members: number }>>([]);
  const [subscription, setSubscription] = useState<any>(null);
  const [billingRuntime, setBillingRuntime] = useState<BillingRuntime>({ provider: 'none', available: false });
  const [seatSummary, setSeatSummary] = useState<SeatSummary | null>(null);
  const [readiness, setReadiness] = useState<WorkspaceReadiness | null>(null);
  const [inviteEmail, setInviteEmail] = useState('');
  const [inviteEmailError, setInviteEmailError] = useState('');
  const [inviteRole, setInviteRole] = useState('viewer');
  const [message, setMessage] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [activeTab, setActiveTab] = useState<TabKey>('general');
  const [wsName, setWsName] = useState('');
  const [timezone, setTimezone] = useState('UTC');
  const [currency, setCurrency] = useState('USD');

  // --- Governance Guard state (Screen 11) ---
  const [gov, setGov] = useState<GovernanceState>(emptyGovernanceState());
  const [settingsVersion, setSettingsVersion] = useState<number>(1);
  const [savingSettings, setSavingSettings] = useState(false);
  const [settingsMsg, setSettingsMsg] = useState<{ tone: 'success' | 'error' | 'conflict'; text: string } | null>(null);
  // Which governance dialog is open.
  const [dialog, setDialog] = useState<null | 'policy' | 'changelog' | 'approvals' | 'anomalies' | 'security' | 'anomaly-detail'>(null);
  const [changeLog, setChangeLog] = useState<{ state: LoadState; items: ChangeLogItem[]; filter: string }>({ state: 'idle', items: [], filter: 'all' });
  const [approvals, setApprovals] = useState<{ state: LoadState; items: Approval[] }>({ state: 'idle', items: [] });
  const [anomalies, setAnomalies] = useState<{ state: LoadState; items: GovernanceAnomaly[] }>({ state: 'idle', items: [] });
  const [anomalyDetail, setAnomalyDetail] = useState<GovernanceAnomaly | null>(null);
  const [actionBusy, setActionBusy] = useState(false);
  const [govMessage, setGovMessage] = useState<{ tone: 'success' | 'error'; text: string } | null>(null);
  // Proposed security-policy change awaiting confirmation (critical-change flow).
  const [securityDraft, setSecurityDraft] = useState<{ mfa_enforcement: string; reauthentication_minutes: number } | null>(null);

  const fallbackWorkspace = user?.memberships?.[0]?.workspace ?? null;
  const resolvedWorkspace = user?.current_workspace ?? fallbackWorkspace;
  const hasWorkspace = Boolean(resolvedWorkspace?.id);
  useEffect(() => {
    if (resolvedWorkspace?.name) setWsName(resolvedWorkspace.name);
  }, [resolvedWorkspace?.name]);

  // All settings/governance traffic goes through the same-origin proxy at /api/<path>.
  // authHeaders() carries Authorization + X-Workspace-Id (and X-CSRF-Token once ready);
  // the proxy forwards them to the backend and preserves its status code. GET reads never
  // require CSRF; mutations call ensureCsrf() first so the token is present to forward.
  async function call(path: string, init?: RequestInit) {
    return fetch(`/api${path}`, { cache: 'no-store', ...init, headers: { ...(init?.headers ?? {}), ...authHeaders() } });
  }

  // A state-changing governance request needs the anti-CSRF token bootstrapped.
  // authHeaders() already carries it once ready; ensure it is before mutating.
  async function ensureCsrf() {
    if (!csrfReady) {
      await refreshCsrfToken();
    }
  }

  // loadStateFor() lives in governance-view-model.ts (single source of truth) and is
  // imported above; the dialog loaders below reuse it for the same 200 / 401-403 / error mapping.

  async function loadAll() {
    if (!resolvedWorkspace?.id) return;
    try {
      const [membersRes, inviteRes, seatsRes, subscriptionRes, plansRes, readinessRes] = await Promise.all([
        call('/workspace/members'),
        call('/workspace/invitations'),
        call('/team/seats'),
        call('/billing/subscription'),
        call('/billing/plans'),
        call('/system/readiness'),
      ]);
      if (membersRes.ok) setMembers((await membersRes.json()).members ?? []);
      if (inviteRes.ok) setInvitations((await inviteRes.json()).invitations ?? []);
      if (seatsRes.ok) setSeatSummary(await seatsRes.json());
      if (subscriptionRes.ok) {
        const payload = await subscriptionRes.json();
        setSubscription(payload.subscription ?? null);
        setBillingRuntime(payload.billing ?? { provider: 'none', available: false });
      }
      if (plansRes.ok) setPlans((await plansRes.json()).plans ?? []);
      if (readinessRes.ok) setReadiness(await readinessRes.json());
    } catch {
      // Transport failure: leave the per-section empty states ("No team members loaded",
      // "Not Configured") in place rather than fabricating data. Never throws out of the effect.
    }
  }

  useEffect(() => { if (loading) return; void loadAll(); }, [loading, resolvedWorkspace?.id]);

  // Governance Guard: read-only load of settings, security policy, posture, and summary.
  // A page load / refresh performs GETs only — it never evaluates or persists anomalies as a
  // side effect. The state machine (workspace guard, per-endpoint permission_denied vs error,
  // fail-closed transport handling) lives in fetchGovernanceState() so it is unit-tested
  // independently of React and ALWAYS resolves to a terminal state — a read/no-admin user can
  // never leave a card stuck on 'loading' via an early return, and a failed/forbidden read is
  // rendered as an explicit Unavailable/Restricted state rather than a spinner.
  async function loadGovernance() {
    if (hasWorkspace) setGov(emptyGovernanceState());
    const next = await fetchGovernanceState({ hasWorkspace, call });
    setGov(next);
    // Seed the editable General-form fields from the canonical settings payload (read path).
    const s = next.settings;
    if (s) {
      setSettingsVersion(Number(s.version ?? 1));
      if (typeof s.timezone === 'string') setTimezone(s.timezone);
      if (typeof s.currency === 'string') setCurrency(s.currency);
      if (typeof s.name === 'string' && s.name) setWsName(s.name);
    }
  }

  // Re-run whenever auth finishes resolving OR the active workspace changes, so a workspace
  // switch retriggers every Screen 11 read. Gated on `loading` so we wait for the session
  // before deciding "no workspace" (which is an explicit unavailable state, not a spinner).
  useEffect(() => { if (loading) return; void loadGovernance(); }, [loading, resolvedWorkspace?.id]);

  const settingsDirty = Boolean(
    gov.settings &&
    (wsName.trim() !== (gov.settings.name ?? '') || timezone !== gov.settings.timezone || currency !== gov.settings.currency),
  );
  const canManageSettings = gov.settings?.can_manage ?? false;
  const canManageSecurity = gov.summary?.can_manage ?? gov.security?.can_manage ?? false;

  async function saveWorkspaceSettings() {
    if (!settingsDirty || savingSettings) return;
    setSavingSettings(true);
    setSettingsMsg(null);
    await ensureCsrf();
    const res = await call('/workspace/settings', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: wsName.trim(), timezone, currency, expected_version: settingsVersion }),
    });
    setSavingSettings(false);
    if (res.ok) {
      const payload = await res.json();
      setSettingsVersion(Number(payload.version ?? settingsVersion + 1));
      setSettingsMsg({ tone: 'success', text: 'Workspace settings saved.' });
      void loadGovernance();
      return;
    }
    if (res.status === 409) {
      setSettingsMsg({ tone: 'conflict', text: 'Settings changed since you opened this page. Refresh the current configuration before retrying.' });
      return;
    }
    if (res.status === 401 || res.status === 403) {
      setSettingsMsg({ tone: 'error', text: 'You do not have permission to change workspace settings.' });
      return;
    }
    setSettingsMsg({ tone: 'error', text: 'Could not save workspace settings. No change was applied.' });
  }

  // Submit a security-policy change. LOW/MEDIUM applies directly; HIGH/CRITICAL
  // enters the approval workflow — the confirmation dialog collects intent first.
  async function submitSecurityChange(proposed: { mfa_enforcement: string; reauthentication_minutes: number }) {
    setActionBusy(true);
    setGovMessage(null);
    await ensureCsrf();
    const res = await call('/workspace/security-settings/change', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(proposed),
    });
    setActionBusy(false);
    setSecurityDraft(null);
    setDialog(null);
    if (res.ok) {
      const payload = await res.json();
      if (payload.status === 'pending_approval') {
        setGovMessage({ tone: 'success', text: `Change classified ${String(payload.risk_level).toUpperCase()} and submitted for approval. The current policy is unchanged until approved.` });
      } else {
        setGovMessage({ tone: 'success', text: 'Security policy updated.' });
      }
      void loadGovernance();
      return;
    }
    if (res.status === 409) {
      setGovMessage({ tone: 'error', text: 'A security policy change is already awaiting approval.' });
      return;
    }
    if (res.status === 401 || res.status === 403) {
      setGovMessage({ tone: 'error', text: 'Reauthentication or the security management permission is required.' });
      return;
    }
    setGovMessage({ tone: 'error', text: 'Could not submit the security policy change.' });
  }

  async function runEvaluation() {
    setActionBusy(true);
    setGovMessage(null);
    await ensureCsrf();
    const res = await call('/workspace/governance/evaluate', { method: 'POST' });
    setActionBusy(false);
    if (res.ok) {
      const payload = await res.json();
      setGovMessage({ tone: 'success', text: `Evaluation complete — ${payload.findings_count} finding(s) across ${payload.events_scanned} events.` });
      void loadGovernance();
    } else if (res.status === 401 || res.status === 403) {
      setGovMessage({ tone: 'error', text: 'The security management permission is required to run evaluation.' });
    } else {
      setGovMessage({ tone: 'error', text: 'Governance evaluation failed.' });
    }
  }

  async function openChangeLog() {
    setDialog('changelog');
    setChangeLog((prev) => ({ ...prev, state: 'loading' }));
    const res = await call('/workspace/governance/changes');
    if (res.ok) {
      const payload = await res.json();
      setChangeLog({ state: 'loaded', items: payload.changes ?? [], filter: 'all' });
    } else {
      setChangeLog({ state: loadStateFor(res.status), items: [], filter: 'all' });
    }
  }

  async function openApprovals() {
    setDialog('approvals');
    setApprovals({ state: 'loading', items: [] });
    const res = await call('/workspace/governance/approvals');
    if (res.ok) {
      const payload = await res.json();
      setApprovals({ state: 'loaded', items: payload.approvals ?? [] });
    } else {
      setApprovals({ state: loadStateFor(res.status), items: [] });
    }
  }

  async function openAnomalies() {
    setDialog('anomalies');
    setAnomalies({ state: 'loading', items: [] });
    const res = await call('/workspace/governance/anomalies');
    if (res.ok) {
      const payload = await res.json();
      setAnomalies({ state: 'loaded', items: payload.anomalies ?? [] });
    } else {
      setAnomalies({ state: loadStateFor(res.status), items: [] });
    }
  }

  async function decideApproval(id: string, decision: 'approve' | 'reject') {
    setActionBusy(true);
    setGovMessage(null);
    await ensureCsrf();
    const res = await call(`/workspace/governance/approvals/${id}/decision`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ decision }),
    });
    setActionBusy(false);
    if (res.ok) {
      setGovMessage({ tone: 'success', text: decision === 'approve' ? 'Change approved and executed.' : 'Change rejected. The current policy is unchanged.' });
      await openApprovals();
      void loadGovernance();
    } else if (res.status === 403) {
      const payload = await res.json().catch(() => ({}));
      const code = payload?.detail?.code;
      setGovMessage({ tone: 'error', text: code === 'SELF_APPROVAL_FORBIDDEN' ? 'You cannot approve a change you requested. A different authorized approver must approve it.' : 'Reauthentication or permission is required.' });
    } else if (res.status === 409) {
      setGovMessage({ tone: 'error', text: 'This change request has already been decided.' });
      await openApprovals();
    } else {
      setGovMessage({ tone: 'error', text: 'Could not record the approval decision.' });
    }
  }

  async function openAnomalyDetail(id: string) {
    setActionBusy(true);
    const res = await call(`/workspace/governance/anomalies/${id}`);
    setActionBusy(false);
    if (res.ok) {
      setAnomalyDetail(await res.json());
      setDialog('anomaly-detail');
    }
  }

  async function setAnomalyStatus(id: string, status: 'acknowledged' | 'resolved' | 'dismissed') {
    setActionBusy(true);
    await ensureCsrf();
    const res = await call(`/workspace/governance/anomalies/${id}/status`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status }),
    });
    setActionBusy(false);
    if (res.ok) {
      setDialog('anomalies');
      setAnomalyDetail(null);
      await openAnomalies();
      void loadGovernance();
      setGovMessage({ tone: 'success', text: `Finding ${status}.` });
    } else {
      setGovMessage({ tone: 'error', text: 'Could not update the finding.' });
    }
  }

  function validateInviteEmail(value: string): string {
    if (!value.trim()) return 'Email address is required.';
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value.trim())) return 'Enter a valid email address.';
    return '';
  }

  async function inviteMember() {
    const emailErr = validateInviteEmail(inviteEmail);
    setInviteEmailError(emailErr);
    if (!hasWorkspace || emailErr) return;
    setSubmitting(true);
    const res = await call('/workspace/invitations', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: inviteEmail, role: inviteRole }),
    });
    setMessage(res.ok ? `Invitation sent to ${inviteEmail}.` : 'Invitation failed.');
    setSubmitting(false);
    if (res.ok) { setInviteEmail(''); void loadAll(); }
  }

  async function updateRole(memberId: string, role: string) {
    const res = await call(`/workspace/members/${memberId}`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ role }),
    });
    setMessage(res.ok ? 'Role updated.' : 'Role update failed.');
    if (res.ok) void loadAll();
  }

  async function removeMember(memberId: string) {
    if (!window.confirm('Remove this member from the workspace?')) return;
    const res = await call(`/workspace/members/${memberId}`, { method: 'DELETE' });
    setMessage(res.ok ? 'Member removed.' : 'Member removal failed.');
    if (res.ok) void loadAll();
  }

  async function resendInvitation(invitationId: string) {
    if (!window.confirm('Resend this pending invitation?')) return;
    const res = await call(`/workspace/invitations/${invitationId}/resend`, { method: 'POST' });
    setMessage(res.ok ? 'Invitation resent.' : 'Unable to resend invitation.');
    if (res.ok) void loadAll();
  }

  async function revokeInvitation(invitationId: string) {
    if (!window.confirm('Revoke this pending invitation?')) return;
    const res = await call(`/workspace/invitations/${invitationId}`, { method: 'DELETE' });
    setMessage(res.ok ? 'Invitation revoked.' : 'Unable to revoke invitation.');
    if (res.ok) void loadAll();
  }

  async function startCheckout(planKey: string) {
    const res = await call('/billing/checkout-session', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ plan_key: planKey }),
    });
    if (!res.ok) { setMessage('Unable to start checkout.'); return; }
    const payload = await res.json();
    window.location.href = payload.checkout_url;
  }

  async function openBillingPortal() {
    setSubmitting(true);
    const res = await call('/billing/portal-session', { method: 'POST', headers: { 'Content-Type': 'application/json' } });
    setSubmitting(false);
    if (!res.ok) { setMessage('Unable to open billing portal. Contact support if billing is active.'); return; }
    const payload = await res.json();
    if (payload.portal_url) {
      window.location.href = payload.portal_url;
    } else {
      setMessage('Billing portal URL not returned. Contact support.');
    }
  }

  const billingStatus = subscription?.status ?? 'Not Configured';
  const billingAvailable = billingEnabled(billingRuntime);
  const nearSeatLimit = seatSummary ? seatSummary.used >= seatSummary.limit : false;
  const workspaceStatus = hasWorkspace ? (readiness?.status === 'pass' ? 'Active' : readiness ? 'Review' : 'Loading') : 'Not Configured';
  const teamCount = members.length + invitations.filter((i) => i.status !== 'revoked').length;
  const billingStatusDisplay = billingAvailable
    ? (subscription?.status ? String(subscription.status).charAt(0).toUpperCase() + String(subscription.status).slice(1) : 'Not Configured')
    : 'Not Configured';
  const securityPosture = readiness?.status === 'pass' ? 'Good' : readiness ? 'Review' : 'Not Configured';

  return (
    <main className="productPage">
      
      <section className="featureSection">
        <div className="sectionHeader">
          <div>
            <p className="eyebrow">Settings</p>
            <h1 style={{ margin: '0.25rem 0 0.5rem' }}>Settings</h1>
            <p className="lede">Manage workspace, team, security, billing, and notification preferences.</p>
          </div>
        </div>

        
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: '1rem', marginTop: '1.25rem' }}>
          <MetricCard label="Workspace Status" value={<StatusPill status={workspaceStatus} />} sub={resolvedWorkspace?.name ?? 'No workspace'} />
          <MetricCard label="Team Members" value={teamCount} sub={`${members.length} active 路 ${invitations.length} invited`} />
          <MetricCard label="Security Posture" value={<StatusPill status={securityPosture} />} sub={readiness ? `${readiness.blocking_failures?.length ?? 0} blocking issues` : 'Not evaluated'} />
          <MetricCard label="Billing Status" value={<StatusPill status={billingStatusDisplay} />} sub={billingAvailable ? `Provider: ${billingRuntime.provider ?? 'unknown'}` : 'Not configured'} />
        </div>

        
        <div role="tablist" aria-label="Settings tabs" style={{ display: 'flex', gap: '0.25rem', marginTop: '1.5rem', borderBottom: '1px solid #21262d', paddingBottom: 0 }}>
          {TABS.map(({ key, label }) => (
            <button
              key={key}
              type="button"
              role="tab"
              aria-selected={activeTab === key}
              onClick={() => setActiveTab(key)}
              style={{
                background: 'none',
                border: 'none',
                borderBottom: activeTab === key ? '2px solid #3b82f6' : '2px solid transparent',
                color: activeTab === key ? '#93c5fd' : '#8b949e',
                cursor: 'pointer',
                fontWeight: activeTab === key ? 700 : 500,
                fontSize: '0.88rem',
                padding: '0.6rem 1rem',
                marginBottom: '-1px',
              }}
            >
              {label}
            </button>
          ))}
        </div>
      </section>

      
      {activeTab === 'general' ? (
        <section className="featureSection">
          {govMessage ? (
            <p role="status" style={{ marginTop: '0.75rem', fontSize: '0.82rem', color: govMessage.tone === 'error' ? '#f87171' : '#4ade80' }}>{govMessage.text}</p>
          ) : null}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(340px, 1fr))', gap: '1rem', marginTop: '1rem' }}>

            {/* Workspace Settings */}
            <SectionCard title="Workspace Settings">
              {!hasWorkspace ? (
                <EmptyState
                  title="Workspace settings unavailable"
                  message="Workspace profile could not be loaded."
                  action="Refresh"
                  disabled={loading}
                />
              ) : (
                <>
                  <FieldRow label="Workspace Name" value={
                    <input
                      value={wsName}
                      onChange={(e) => setWsName(e.target.value)}
                      disabled={!canManageSettings}
                      aria-label="Workspace Name"
                      style={{ width: '100%', background: '#0d1117', border: '1px solid #30363d', borderRadius: 8, color: '#e6edf3', padding: '0.45rem 0.65rem', fontSize: '0.85rem', opacity: canManageSettings ? 1 : 0.6 }}
                    />
                  } />
                  <FieldRow label="Workspace ID" readOnly value={<code style={{ fontSize: '0.8rem', color: '#8b949e' }}>{resolvedWorkspace?.id ?? '-'}</code>} />
                  <FieldRow label="Timezone" value={
                    <select
                      value={timezone}
                      onChange={(e) => setTimezone(e.target.value)}
                      disabled={!canManageSettings}
                      aria-label="Timezone"
                      style={{ background: '#0d1117', border: '1px solid #30363d', borderRadius: 8, color: '#e6edf3', padding: '0.4rem 0.6rem', fontSize: '0.85rem', width: '100%' }}
                    >
                      {(gov.settings?.allowed_timezones ?? ['UTC']).map((tz) => <option key={tz} value={tz}>{tz}</option>)}
                    </select>
                  } />
                  <FieldRow label="Currency" value={
                    <select
                      value={currency}
                      onChange={(e) => setCurrency(e.target.value)}
                      disabled={!canManageSettings}
                      aria-label="Currency"
                      style={{ background: '#0d1117', border: '1px solid #30363d', borderRadius: 8, color: '#e6edf3', padding: '0.4rem 0.6rem', fontSize: '0.85rem', width: '100%' }}
                    >
                      {(gov.settings?.allowed_currencies ?? ['USD']).map((c) => <option key={c} value={c}>{c}</option>)}
                    </select>
                  } />
                  <FieldRow label="Default Monitoring Mode" value={<StatusPill status={liveModeConfigured ? 'Live' : 'Sample'} />} note={liveModeConfigured ? 'Connected to live data sources' : 'Using sample data only'} />
                  {!canManageSettings ? (
                    <p className="muted" style={{ fontSize: '0.78rem', marginTop: '0.6rem' }}>You need workspace administration permission to edit these settings.</p>
                  ) : null}
                  <div style={{ marginTop: '0.85rem', display: 'flex', gap: '0.6rem', alignItems: 'center' }}>
                    <button
                      className="btn btn-primary"
                      type="button"
                      onClick={() => void saveWorkspaceSettings()}
                      disabled={!canManageSettings || !settingsDirty || savingSettings}
                      style={{ opacity: (!canManageSettings || !settingsDirty) ? 0.5 : 1, cursor: (!canManageSettings || !settingsDirty) ? 'not-allowed' : 'pointer' }}
                    >
                      {savingSettings ? 'Saving…' : 'Save Changes'}
                    </button>
                    {settingsMsg ? (
                      <span role={settingsMsg.tone === 'success' ? 'status' : 'alert'} style={{ fontSize: '0.8rem', color: settingsMsg.tone === 'success' ? '#4ade80' : settingsMsg.tone === 'conflict' ? '#fbbf24' : '#f87171' }}>
                        {settingsMsg.text}
                        {settingsMsg.tone === 'conflict' ? (
                          <button className="btn btn-ghost" type="button" style={{ marginLeft: '0.5rem', fontSize: '0.76rem', padding: '0.2rem 0.5rem' }} onClick={() => void loadGovernance()}>Refresh</button>
                        ) : null}
                      </span>
                    ) : null}
                  </div>
                </>
              )}
            </SectionCard>

            {/* Security Settings (canonical workspace_auth_policies) */}
            <SectionCard title="Security Settings" action={canManageSecurity ? (
              <button className="btn btn-ghost" type="button" style={{ fontSize: '0.78rem', padding: '0.25rem 0.6rem' }}
                onClick={() => { setSecurityDraft({ mfa_enforcement: gov.security?.mfa_enforcement ?? 'optional', reauthentication_minutes: gov.security?.reauthentication_minutes ?? 30 }); setDialog('security'); }}>
                Change security policy
              </button>
            ) : undefined}>
              {gov.securityState === 'error' ? (
                <p role="alert" style={{ color: '#f87171', fontSize: '0.85rem' }}>Security settings unavailable.</p>
              ) : gov.securityState === 'permission_denied' ? (
                <p className="muted" style={{ fontSize: '0.85rem' }}>Requires security management permission.</p>
              ) : gov.securityState === 'loading' ? (
                <p className="muted" style={{ fontSize: '0.85rem' }}>Loading security policy…</p>
              ) : (
                <>
                  <FieldRow label="MFA Enforcement" value={
                    <span className={'pill pill-' + (gov.security?.mfa_enforcement === 'all_members' ? 'success' : gov.security?.mfa_enforcement === 'administrators' ? 'warning' : 'neutral')}>
                      {gov.security?.mfa_enforcement === 'all_members' ? 'All members' : gov.security?.mfa_enforcement === 'administrators' ? 'Administrators' : 'Optional'}
                    </span>
                  } note="Workspace policy — reused from canonical MFA/session policy." />
                  <FieldRow label="Session Timeout" value={<span>{gov.security ? `${gov.security.reauthentication_minutes} minutes` : '—'}</span>} note="Reauthentication window for sensitive actions." />
                  <FieldRow label="IP Allowlist" value={<StatusPill status="Not configured" />} note="IP allowlist enforcement is not available in this deployment." />
                  <FieldRow label="Audit Logging" value={<span className="pill pill-success">Always on</span>} note="Append-only, hash-chained. Cannot be disabled." />
                  <FieldRow label="Data Encryption" value={<span className="pill pill-info">At rest &amp; in transit</span>} note="Enforced at the infrastructure layer." />
                </>
              )}
            </SectionCard>

            {/* AI Policy Impact */}
            <SectionCard title="AI Policy Impact">
              {gov.postureState === 'error' ? (
                <p role="alert" style={{ color: '#f87171', fontSize: '0.85rem' }}>Policy analysis unavailable.</p>
              ) : gov.postureState === 'permission_denied' ? (
                <p className="muted" style={{ fontSize: '0.85rem' }}>Requires security management permission.</p>
              ) : (
                <>
                  <p className="muted" style={{ marginTop: 0, fontSize: '0.82rem' }}>These settings reduce risk and improve your security posture.</p>
                  <div style={{ display: 'flex', gap: '1.5rem', margin: '0.75rem 0' }}>
                    <div>
                      <p className="sectionEyebrow" style={{ margin: '0 0 0.2rem' }}>Risk Reduction</p>
                      <p style={{ margin: 0, fontSize: '1.35rem', fontWeight: 700 }}>{posturePercentLabel(gov.posture, gov.postureState)}</p>
                    </div>
                    <div>
                      <p className="sectionEyebrow" style={{ margin: '0 0 0.2rem' }}>Access Risk</p>
                      <p style={{ margin: 0 }}><span className={'pill pill-' + riskPillVariant(gov.posture?.access_risk ?? 'unknown')}>{accessRiskLabel(gov.posture, gov.postureState)}</span></p>
                    </div>
                  </div>
                  {gov.posture?.evidence_status === 'insufficient' ? (
                    <p style={{ color: '#fbbf24', fontSize: '0.78rem', margin: '0 0 0.5rem' }}>Access evidence not evaluated — run Governance Guard evaluation for a complete score.</p>
                  ) : null}
                  <button className="btn btn-secondary" type="button" onClick={() => setDialog('policy')} disabled={gov.postureState !== 'loaded'}>
                    View policy impact →
                  </button>
                </>
              )}
            </SectionCard>

            {/* Governance Guard */}
            <SectionCard title="Governance Guard">
              {(() => {
                const anomaly = anomalyStateLabel(gov.summary, gov.summaryState);
                const safeguard = changeSafeguardLabel(gov.summary, gov.summaryState);
                const pending = gov.summary?.pending_change_requests ?? 0;
                return (
                  <>
                    <div style={{ marginBottom: '0.75rem' }}>
                      <p className="sectionEyebrow" style={{ margin: '0 0 0.2rem' }}>Access Anomalies</p>
                      <p style={{ margin: 0, fontSize: '1.15rem', fontWeight: 700 }}>
                        <span className={'pill pill-' + anomaly.tone} style={{ fontSize: '0.95rem' }}>{anomaly.value}</span>
                      </p>
                      <p className="muted" style={{ margin: '0.3rem 0 0', fontSize: '0.8rem' }}>{anomaly.detail}</p>
                    </div>
                    <div style={{ marginBottom: '0.75rem' }}>
                      <p className="sectionEyebrow" style={{ margin: '0 0 0.2rem' }}>Change Safeguards</p>
                      <p style={{ margin: 0 }}><span className={'pill pill-' + safeguard.tone}>{safeguard.value}</span></p>
                      <p className="muted" style={{ margin: '0.3rem 0 0', fontSize: '0.8rem' }}>{safeguard.detail}</p>
                    </div>
                    {pending > 0 ? (
                      <p style={{ margin: '0 0 0.6rem', fontSize: '0.82rem', color: '#fbbf24' }}>
                        {pending} security change{pending === 1 ? '' : 's'} awaiting approval.{' '}
                        <button className="btn btn-ghost" type="button" style={{ fontSize: '0.78rem', padding: '0.2rem 0.5rem' }} onClick={() => void openApprovals()}>Review approvals</button>
                      </p>
                    ) : null}
                    <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
                      <button className="btn btn-ghost" type="button" onClick={() => void openAnomalies()} disabled={gov.summaryState !== 'loaded'}>View anomalies</button>
                      <button className="btn btn-ghost" type="button" onClick={() => void openChangeLog()}>View change log →</button>
                      {canManageSecurity ? (
                        <button className="btn btn-secondary" type="button" onClick={() => void runEvaluation()} disabled={actionBusy}>{actionBusy ? 'Evaluating…' : 'Run evaluation'}</button>
                      ) : null}
                    </div>
                  </>
                );
              })()}
            </SectionCard>

          </div>

          {/* ---- Governance dialogs ---- */}
          <GovernanceDialog open={dialog === 'policy'} title="Policy impact" onClose={() => setDialog(null)}>
            {gov.posture ? (
              <div>
                <div style={{ display: 'flex', gap: '2rem', marginBottom: '1rem' }}>
                  <div><p className="sectionEyebrow">Risk reduction</p><p style={{ fontSize: '1.3rem', fontWeight: 700, margin: 0 }}>{gov.posture.risk_reduction_percent}%</p></div>
                  <div><p className="sectionEyebrow">Access risk</p><p style={{ margin: 0 }}><span className={'pill pill-' + riskPillVariant(gov.posture.access_risk)}>{accessRiskLabel(gov.posture, 'loaded')}</span></p></div>
                  <div><p className="sectionEyebrow">Controls passing</p><p style={{ margin: 0 }}>{gov.posture.controls_passing} / {gov.posture.controls_total}</p></div>
                </div>
                <p className="sectionEyebrow">Controls evaluated</p>
                <div style={{ overflowX: 'auto' }}>
                  <table>
                    <thead><tr><th>Control</th><th>Result</th><th>Evidence</th></tr></thead>
                    <tbody>
                      {gov.posture.controls.map((c) => (
                        <tr key={c.key}>
                          <td>{c.label}<span className="tableMeta">{c.detail}</span></td>
                          <td><span className={'pill pill-' + (c.status === 'pass' ? 'success' : c.status === 'fail' ? 'danger' : c.status === 'attention' ? 'warning' : 'neutral')}>{c.status === 'unsupported' ? 'Unsupported' : c.status === 'unknown' ? 'Not evaluated' : c.status.charAt(0).toUpperCase() + c.status.slice(1)}</span></td>
                          <td><span className="muted" style={{ fontSize: '0.78rem' }}>{c.evidence_source === 'configuration' ? 'Configuration' : c.evidence_source === 'access_evidence' ? 'Access evidence' : 'Unavailable'}</span></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {gov.posture.recommendations.length > 0 ? (
                  <>
                    <p className="sectionEyebrow" style={{ marginTop: '1rem' }}>Recommended actions</p>
                    <ul style={{ fontSize: '0.85rem' }}>
                      {gov.posture.recommendations.map((r) => (
                        <li key={r.key} style={{ marginBottom: '0.35rem' }}><span className={'pill pill-' + riskPillVariant(r.severity)} style={{ marginRight: '0.4rem' }}>{r.severity}</span>{r.recommended_action}</li>
                      ))}
                    </ul>
                  </>
                ) : null}
                <p className="muted" style={{ fontSize: '0.76rem', marginTop: '0.75rem' }}>Last evaluated: {gov.posture.last_evaluated_at ? new Date(gov.posture.last_evaluated_at).toLocaleString() : 'Not evaluated'} · Calculated: {new Date(gov.posture.calculated_at).toLocaleString()}</p>
              </div>
            ) : <p className="muted">Policy analysis unavailable.</p>}
          </GovernanceDialog>

          <GovernanceDialog open={dialog === 'changelog'} title="Change log" onClose={() => setDialog(null)} maxWidth={860}>
            <div style={{ display: 'flex', gap: '0.35rem', flexWrap: 'wrap', marginBottom: '0.75rem' }}>
              {['all', 'critical', 'high', 'medium', 'low', 'pending', 'completed', 'rejected'].map((f) => (
                <button key={f} type="button" className={'btn btn-ghost'} onClick={() => setChangeLog((p) => ({ ...p, filter: f }))}
                  style={{ fontSize: '0.76rem', padding: '0.2rem 0.55rem', borderBottom: changeLog.filter === f ? '2px solid #3b82f6' : '2px solid transparent', textTransform: 'capitalize' }}>{f}</button>
              ))}
            </div>
            {changeLog.state === 'loading' ? <p className="muted">Loading change log…</p>
              : changeLog.state === 'error' ? <p role="alert" style={{ color: '#f87171' }}>Change log unavailable.</p>
              : changeLog.state === 'permission_denied' ? <p className="muted">Requires security management permission.</p>
              : (
                <div style={{ overflowX: 'auto' }}>
                  <table>
                    <thead><tr><th>Time</th><th>Actor</th><th>Change</th><th>Risk</th><th>Approval</th><th>Result</th><th>Source</th></tr></thead>
                    <tbody>
                      {changeLog.items
                        .filter((it) => changeLog.filter === 'all'
                          || (['critical', 'high', 'medium', 'low'].includes(changeLog.filter) && it.risk_level === changeLog.filter)
                          || (['pending', 'completed', 'rejected'].includes(changeLog.filter) && it.approval === changeLog.filter))
                        .map((it) => (
                          <tr key={it.id}>
                            <td><span className="muted" style={{ fontSize: '0.78rem' }}>{it.timestamp ? new Date(it.timestamp).toLocaleString() : '-'}</span></td>
                            <td>{it.actor}</td>
                            <td>{it.change}<span className="tableMeta">{it.target ?? ''}</span></td>
                            <td><span className={'pill pill-' + riskPillVariant(it.risk_level)}>{it.risk_level}</span></td>
                            <td><span className="muted" style={{ fontSize: '0.78rem' }}>{it.approval.replace('_', ' ')}</span></td>
                            <td><span className="muted" style={{ fontSize: '0.78rem' }}>{it.result}</span></td>
                            <td><span className="muted" style={{ fontSize: '0.78rem' }}>{it.source_ip ?? 'Current session'}</span></td>
                          </tr>
                        ))}
                    </tbody>
                  </table>
                  {changeLog.items.length === 0 ? <p className="muted" style={{ marginTop: '0.75rem' }}>No governance changes recorded yet.</p> : null}
                </div>
              )}
          </GovernanceDialog>

          <GovernanceDialog open={dialog === 'approvals'} title="Pending approvals" onClose={() => setDialog(null)} maxWidth={760}>
            {approvals.state === 'loading' ? <p className="muted">Loading approvals…</p>
              : approvals.state === 'error' ? <p role="alert" style={{ color: '#f87171' }}>Approvals unavailable.</p>
              : approvals.state === 'permission_denied' ? <p className="muted">Requires security management permission.</p>
              : approvals.items.length === 0 ? <p className="muted">No change requests.</p>
              : (
                <div style={{ display: 'grid', gap: '0.75rem' }}>
                  {approvals.items.map((a) => (
                    <article key={a.id} className="dataCard" style={{ margin: 0 }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '0.5rem' }}>
                        <span className={'pill pill-' + riskPillVariant(a.risk_level)}>{a.risk_level}</span>
                        <span className="muted" style={{ fontSize: '0.78rem' }}>{a.status}</span>
                      </div>
                      <p style={{ margin: '0.5rem 0 0.25rem', fontSize: '0.85rem' }}>{a.risk_reason}</p>
                      <p className="muted" style={{ fontSize: '0.78rem', margin: 0 }}>
                        Requested by {a.requested_by} · {a.requested_at ? new Date(a.requested_at).toLocaleString() : ''}
                      </p>
                      <p className="muted" style={{ fontSize: '0.78rem', margin: '0.25rem 0 0' }}>
                        {JSON.stringify(a.before_value)} → {JSON.stringify(a.proposed_value)}
                      </p>
                      {a.status === 'pending' ? (
                        <div style={{ display: 'flex', gap: '0.5rem', marginTop: '0.6rem' }}>
                          <button className="btn btn-primary" type="button" disabled={actionBusy} onClick={() => void decideApproval(a.id, 'approve')}>Approve</button>
                          <button className="btn btn-danger" type="button" disabled={actionBusy} onClick={() => void decideApproval(a.id, 'reject')}>Reject</button>
                        </div>
                      ) : a.status === 'completed' ? (
                        <p className="muted" style={{ fontSize: '0.78rem', margin: '0.4rem 0 0' }}>Approved by {a.approved_by ?? 'authorized approver'} · executed {a.executed_at ? new Date(a.executed_at).toLocaleString() : ''}</p>
                      ) : a.status === 'failed' ? (
                        <p style={{ fontSize: '0.78rem', margin: '0.4rem 0 0', color: '#f87171' }}>Execution failed: {a.failure_reason}</p>
                      ) : null}
                    </article>
                  ))}
                </div>
              )}
          </GovernanceDialog>

          <GovernanceDialog open={dialog === 'anomalies'} title="Access anomalies" onClose={() => setDialog(null)} maxWidth={760}>
            {anomalies.state === 'loading' ? <p className="muted">Loading findings…</p>
              : anomalies.state === 'error' ? <p role="alert" style={{ color: '#f87171' }}>Findings unavailable.</p>
              : anomalies.state === 'permission_denied' ? <p className="muted">Requires security management permission.</p>
              : anomalies.items.length === 0 ? <p className="muted">No access anomalies recorded. Run evaluation to check for new findings.</p>
              : (
                <div style={{ overflowX: 'auto' }}>
                  <table>
                    <thead><tr><th>Severity</th><th>Type</th><th>Actor</th><th>Status</th><th>Last seen</th><th></th></tr></thead>
                    <tbody>
                      {anomalies.items.map((an) => (
                        <tr key={an.id}>
                          <td><span className={'pill pill-' + riskPillVariant(an.severity)}>{an.severity}</span></td>
                          <td>{an.type.replace(/_/g, ' ')}</td>
                          <td>{an.actor}</td>
                          <td><span className="muted" style={{ fontSize: '0.78rem' }}>{an.status}</span></td>
                          <td><span className="muted" style={{ fontSize: '0.78rem' }}>{an.last_seen_at ? new Date(an.last_seen_at).toLocaleString() : '-'}</span></td>
                          <td><button className="btn btn-ghost" type="button" style={{ fontSize: '0.76rem', padding: '0.2rem 0.5rem' }} onClick={() => void openAnomalyDetail(an.id)}>Details</button></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
          </GovernanceDialog>

          <GovernanceDialog open={dialog === 'anomaly-detail'} title="Access anomaly" onClose={() => { setDialog('anomalies'); setAnomalyDetail(null); }}>
            {anomalyDetail ? (
              <div>
                <div style={{ display: 'flex', gap: '0.75rem', alignItems: 'center', marginBottom: '0.75rem' }}>
                  <span className={'pill pill-' + riskPillVariant(anomalyDetail.severity)}>{anomalyDetail.severity}</span>
                  <strong>{anomalyDetail.type.replace(/_/g, ' ')}</strong>
                </div>
                <FieldRow label="Actor" value={anomalyDetail.actor} />
                <FieldRow label="Status" value={anomalyDetail.status} />
                <FieldRow label="Confidence" value={anomalyDetail.confidence != null ? `${anomalyDetail.confidence}%` : '—'} />
                <FieldRow label="First seen" value={anomalyDetail.first_seen_at ? new Date(anomalyDetail.first_seen_at).toLocaleString() : '-'} />
                <FieldRow label="Last seen" value={anomalyDetail.last_seen_at ? new Date(anomalyDetail.last_seen_at).toLocaleString() : '-'} />
                <p className="sectionEyebrow" style={{ marginTop: '0.75rem' }}>Evidence</p>
                <pre style={{ fontSize: '0.76rem', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{JSON.stringify(anomalyDetail.evidence, null, 2)}</pre>
                {anomalyDetail.recommended_actions && anomalyDetail.recommended_actions.length > 0 ? (
                  <>
                    <p className="sectionEyebrow">Recommended next action</p>
                    <ul style={{ fontSize: '0.84rem' }}>{anomalyDetail.recommended_actions.map((a, i) => <li key={i}>{a}</li>)}</ul>
                  </>
                ) : null}
                {canManageSecurity ? (
                  <div style={{ display: 'flex', gap: '0.5rem', marginTop: '0.75rem', flexWrap: 'wrap' }}>
                    <button className="btn btn-secondary" type="button" disabled={actionBusy} onClick={() => void setAnomalyStatus(anomalyDetail.id, 'acknowledged')}>Acknowledge</button>
                    <button className="btn btn-primary" type="button" disabled={actionBusy} onClick={() => void setAnomalyStatus(anomalyDetail.id, 'resolved')}>Mark resolved</button>
                    <button className="btn btn-ghost" type="button" disabled={actionBusy} onClick={() => void setAnomalyStatus(anomalyDetail.id, 'dismissed')}>Dismiss</button>
                  </div>
                ) : null}
              </div>
            ) : null}
          </GovernanceDialog>

          <GovernanceDialog open={dialog === 'security'} title="Change security policy" onClose={() => { setDialog(null); setSecurityDraft(null); }}
            footer={securityDraft ? (
              <>
                <button className="btn btn-ghost" type="button" onClick={() => { setDialog(null); setSecurityDraft(null); }}>Cancel</button>
                <button className="btn btn-primary" type="button" disabled={actionBusy} onClick={() => void submitSecurityChange(securityDraft)}>Submit change</button>
              </>
            ) : undefined}>
            {securityDraft ? (
              <div>
                <p className="muted" style={{ marginTop: 0, fontSize: '0.84rem' }}>
                  Governance Guard classifies each security change. Weakening MFA enforcement or lengthening the session window is a sensitive change and will require a separate-duty approval before it takes effect — the current policy stays unchanged until then.
                </p>
                <label className="label" htmlFor="mfa-enforcement">MFA enforcement</label>
                <select id="mfa-enforcement" value={securityDraft.mfa_enforcement}
                  onChange={(e) => setSecurityDraft({ ...securityDraft, mfa_enforcement: e.target.value })}
                  style={{ display: 'block', width: '100%', background: '#0d1117', border: '1px solid #30363d', borderRadius: 8, color: '#e6edf3', padding: '0.45rem 0.6rem', fontSize: '0.85rem', margin: '0.25rem 0 0.75rem' }}>
                  <option value="optional">Optional</option>
                  <option value="administrators">Administrators</option>
                  <option value="all_members">All members</option>
                </select>
                <label className="label" htmlFor="session-window">Session timeout</label>
                <select id="session-window" value={securityDraft.reauthentication_minutes}
                  onChange={(e) => setSecurityDraft({ ...securityDraft, reauthentication_minutes: Number(e.target.value) })}
                  style={{ display: 'block', width: '100%', background: '#0d1117', border: '1px solid #30363d', borderRadius: 8, color: '#e6edf3', padding: '0.45rem 0.6rem', fontSize: '0.85rem', margin: '0.25rem 0 0' }}>
                  {(gov.security?.session_timeout_options ?? [{ value: 30, label: '30 minutes' }]).map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
              </div>
            ) : null}
          </GovernanceDialog>

        </section>
      ) : null}
      
      {activeTab === 'team' ? (
        <section className="featureSection">
          <div style={{ display: 'grid', gap: '1rem', marginTop: '1rem' }}>

            {/* Invite Member */}
            <SectionCard title="Invite Member">
              <div style={{ display: 'flex', gap: '0.6rem', flexWrap: 'wrap', alignItems: 'flex-end' }}>
                <div style={{ flex: '1 1 240px' }}>
                  <label style={{ display: 'block', fontSize: '0.78rem', color: '#8b949e', marginBottom: '0.3rem', fontWeight: 600 }}>Email</label>
                  <input
                    type="email"
                    value={inviteEmail}
                    onChange={(e) => { setInviteEmail(e.target.value); if (inviteEmailError) setInviteEmailError(validateInviteEmail(e.target.value)); }}
                    onBlur={(e) => setInviteEmailError(validateInviteEmail(e.target.value))}
                    placeholder="teammate@company.com"
                    aria-describedby={inviteEmailError ? 'invite-email-error' : undefined}
                    aria-invalid={!!inviteEmailError}
                    style={{ width: '100%', background: '#0d1117', border: `1px solid ${inviteEmailError ? '#f87171' : '#30363d'}`, borderRadius: 8, color: '#e6edf3', padding: '0.5rem 0.7rem', fontSize: '0.85rem' }}
                  />
                  {inviteEmailError ? <p id="invite-email-error" style={{ color: '#f87171', fontSize: '0.78rem', margin: '0.2rem 0 0' }} role="alert">{inviteEmailError}</p> : null}
                </div>
                <div style={{ flex: '0 1 160px' }}>
                  <label style={{ display: 'block', fontSize: '0.78rem', color: '#8b949e', marginBottom: '0.3rem', fontWeight: 600 }}>Role</label>
                  <select
                    value={inviteRole}
                    onChange={(e) => setInviteRole(e.target.value)}
                    style={{ width: '100%', background: '#0d1117', border: '1px solid #30363d', borderRadius: 8, color: '#e6edf3', padding: '0.5rem 0.7rem', fontSize: '0.85rem' }}
                  >
                    <option value="owner">Owner</option>
                    <option value="admin">Admin</option>
                    <option value="analyst">Analyst</option>
                    <option value="viewer">Viewer</option>
                    <option value="billing">Billing</option>
                  </select>
                </div>
                <button className="btn btn-primary" type="button" onClick={() => void inviteMember()} disabled={submitting || !inviteEmail}>
                  Send Invitation
                </button>
              </div>
              {message ? <p style={{ marginTop: '0.6rem', fontSize: '0.82rem', color: message.includes('failed') || message.includes('Unable') ? '#f87171' : '#4ade80' }}>{message}</p> : null}
            </SectionCard>

            {/* Team Members table */}
            <article className="dataCard">
              <p className="sectionEyebrow">Team Members</p>
              {members.length === 0 && invitations.length === 0 ? (
                <EmptyState
                  title="No team members loaded"
                  message="Team membership data is unavailable or not configured for this workspace."
                  action="Invite Member"
                  disabled={!hasWorkspace}
                />
              ) : (
                <DataTable headers={TEAM_MEMBER_HEADERS}>
                  {members.map((member) => (
                    <tr key={member.id}>
                      <td>
                        <span style={{ fontWeight: 600 }}>{member.full_name || member.email.split('@')[0]}</span>
                      </td>
                      <td><span className="muted">{member.email}</span></td>
                      <td>
                        <span className="pill pill-info" style={{ textTransform: 'capitalize' }}>{member.role}</span>
                      </td>
                      <td><StatusPill status="Active" /></td>
                      <td><span className="muted">{formatDate(member.created_at)}</span></td>
                      <td>
                        <div style={{ display: 'flex', gap: '0.4rem', flexWrap: 'wrap' }}>
                          <select
                            value={member.role}
                            onChange={(e) => void updateRole(member.id, e.target.value)}
                            style={{ background: '#0d1117', border: '1px solid #30363d', borderRadius: 6, color: '#e6edf3', padding: '0.3rem 0.5rem', fontSize: '0.78rem' }}
                          >
                            <option value="owner">Owner</option>
                            <option value="admin">Admin</option>
                            <option value="analyst">Analyst</option>
                            <option value="viewer">Viewer</option>
                          </select>
                          <button className="btn btn-ghost" type="button" style={{ fontSize: '0.78rem', padding: '0.25rem 0.6rem' }} onClick={() => void removeMember(member.id)}>
                            Remove
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                  {invitations.map((inv) => (
                    <tr key={inv.id}>
                      <td><span style={{ color: '#8b949e' }}>-</span></td>
                      <td><span className="muted">{inv.email}</span></td>
                      <td>
                        <span className="pill pill-info" style={{ textTransform: 'capitalize' }}>{inv.role}</span>
                      </td>
                      <td><StatusPill status={inv.status === 'pending' ? 'Invited' : inv.status.charAt(0).toUpperCase() + inv.status.slice(1)} /></td>
                      <td><span className="muted">Expires {formatDate(inv.expires_at)}</span></td>
                      <td>
                        <div style={{ display: 'flex', gap: '0.4rem', flexWrap: 'wrap' }}>
                          <button className="btn btn-ghost" type="button" style={{ fontSize: '0.78rem', padding: '0.25rem 0.6rem' }} onClick={() => void resendInvitation(inv.id)}>
                            Resend
                          </button>
                          <button className="btn btn-danger" type="button" style={{ fontSize: '0.78rem', padding: '0.25rem 0.6rem' }} onClick={() => void revokeInvitation(inv.id)}>
                            Revoke
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </DataTable>
              )}
            </article>

          </div>
        </section>
      ) : null}

      
      {activeTab === 'security' ? (
        <section className="featureSection">
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(340px, 1fr))', gap: '1rem', marginTop: '1rem' }}>

            {/* Authentication Policy */}
            <SectionCard title="Authentication Policy">
              <FieldRow label="MFA Status" value={<StatusPill status="Not Configured" />} note="Enable MFA via /settings/security" />
              <FieldRow label="Session Timeout" value={<StatusPill status="Not Configured" />} note="Contact admin to configure" />
              <FieldRow label="Password Policy" value={<StatusPill status="Not Configured" />} />
              <FieldRow label="SSO Status" value={<StatusPill status="Not Configured" />} note="SSO not yet configured" />
              <div style={{ marginTop: '0.85rem' }}>
                <Link className="btn btn-secondary" href="/settings/security" prefetch={false} style={{ textDecoration: 'none' }}>
                  Configure MFA &amp; Sessions
                </Link>
              </div>
            </SectionCard>

            {/* Access Controls */}
            <SectionCard title="Access Controls">
              <FieldRow label="IP Allowlist" value={<StatusPill status="Not Configured" />} note="No IP restrictions applied" />
              <FieldRow label="Workspace Access Mode" value={<StatusPill status="Not Configured" />} />
              <FieldRow label="Role Enforcement" value={<StatusPill status="Not Configured" />} note="Backend role policy status is not exposed yet" />
              <FieldRow label="Login Alerts" value={<StatusPill status="Not Configured" />} />
            </SectionCard>

            {/* API Security */}
            <SectionCard title="API Security">
              <FieldRow label="API Key Policy" value={<StatusPill status="Not Configured" />} note="Manage keys via /integrations" />
              <FieldRow label="Key Rotation Period" value={<StatusPill status="Not Configured" />} />
              <FieldRow label="Webhook Signing" value={<StatusPill status="Not Configured" />} note="Configure via /integrations" />
              <FieldRow label="Secret Masking" value={<StatusPill status="Required" />} note="Secrets are masked in all outputs" />
              <div style={{ marginTop: '0.85rem' }}>
                <Link className="btn btn-secondary" href="/integrations" prefetch={false} style={{ textDecoration: 'none' }}>
                  Manage API Keys &amp; Webhooks
                </Link>
              </div>
            </SectionCard>

            {/* Audit Logging */}
            <SectionCard title="Audit Logging">
              <FieldRow label="Audit Logging Status" value={<StatusPill status={readiness?.status === 'pass' ? 'Enabled' : 'Not Configured'} />} />
              <FieldRow label="Retention Period" value={<span style={{ color: '#8b949e' }}>90 days (default)</span>} />
              <FieldRow label="Last Readiness Check" value={<span style={{ color: '#8b949e' }}>{readiness?.checked_at ? new Date(readiness.checked_at).toLocaleString() : 'Not available'}</span>} />
              <FieldRow label="Blocking Issues" value={<span style={{ color: readiness && readiness.blocking_failures?.length > 0 ? '#f87171' : '#4ade80' }}>{readiness?.blocking_failures?.length ?? 0} issues</span>} />
              {readiness?.checks && readiness.checks.length > 0 ? (
                <ul style={{ marginTop: '0.5rem', padding: 0, listStyle: 'none', fontSize: '0.82rem' }}>
                  {readiness.checks.map((check) => (
                    <li key={check.key} style={{ color: check.pass ? '#4ade80' : '#f87171', marginBottom: '0.25rem' }}>
                      {check.label}: {check.reason ?? (check.pass ? 'OK' : 'Failed')}
                    </li>
                  ))}
                </ul>
              ) : null}
              <div style={{ marginTop: '0.85rem', display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
                <Link className="btn btn-secondary" href="/evidence" prefetch={false} style={{ textDecoration: 'none' }}>
                  Export Audit Logs
                </Link>
                <button className="btn btn-ghost" type="button" onClick={() => void loadAll()}>
                  Refresh
                </button>
              </div>
            </SectionCard>

          </div>
        </section>
      ) : null}

      
      {activeTab === 'billing' ? (
        <section className="featureSection">
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: '1rem', marginTop: '1rem' }}>

            {/* Plan */}
            <SectionCard title="Plan">
              <FieldRow label="Plan" value={<span style={{ fontWeight: 600 }}>{subscription?.plan_key ? String(subscription.plan_key).toUpperCase() : 'Not Configured'}</span>} />
              <FieldRow label="Status" value={<StatusPill status={billingStatusDisplay} />} />
              <FieldRow label="Billing Email" value={<span style={{ color: '#8b949e' }}>{user?.email ?? 'Not configured'}</span>} />
              <FieldRow label="Renewal Date" value={<span style={{ color: '#8b949e' }}>{subscription?.current_period_end ? formatDate(subscription.current_period_end) : 'Not available'}</span>} />
              {nearSeatLimit ? <p style={{ marginTop: '0.5rem', color: '#fbbf24', fontSize: '0.82rem' }}>Seat limit reached. Contact support to expand access.</p> : null}
              {billingStatus === 'past_due' ? <p style={{ marginTop: '0.5rem', color: '#f87171', fontSize: '0.82rem' }}>Billing is past due. Update billing details to avoid disruption.</p> : null}
              <div style={{ marginTop: '0.85rem' }}>
                <button
                  className="btn btn-secondary"
                  type="button"
                  disabled={submitting || !billingAvailable}
                  style={{ opacity: billingAvailable ? 1 : 0.5, cursor: billingAvailable ? 'pointer' : 'not-allowed' }}
                  onClick={() => void openBillingPortal()}
                  title={billingAvailable ? 'Open billing portal to manage subscription and invoices' : 'Billing provider not configured'}
                >
                  {submitting ? 'Opening...' : 'Manage Billing'}
                </button>
              </div>
            </SectionCard>

            {/* Usage */}
            <SectionCard title="Usage">
              <FieldRow label="Protected Assets Used" value={<span style={{ fontWeight: 600 }}>-</span>} note="Loaded from asset registry" />
              <FieldRow label="Monitored Systems Used" value={<span style={{ fontWeight: 600 }}>-</span>} note="Loaded from monitoring sources" />
              <FieldRow label="Team Seats Used" value={<span style={{ fontWeight: 600 }}>{seatSummary ? `${seatSummary.used} / ${seatSummary.limit}` : 'Loading...'}</span>} />
              <FieldRow label="API Calls Used" value={<span style={{ color: '#8b949e' }}>Not tracked</span>} />
              <FieldRow label="Evidence Storage Used" value={<span style={{ color: '#8b949e' }}>Not tracked</span>} />
            </SectionCard>
            {/* Self-serve launch gate: billing and workspace readiness block broad access until all checks pass */}
            <SectionCard title="Billing Readiness">
              <FieldRow label="Billing Enabled" value={<StatusPill status={billingAvailable ? 'Enabled' : 'Not Configured'} />} />
              <FieldRow label="Payment Provider" value={<span style={{ color: '#8b949e' }}>{billingProviderLabel(billingRuntime)}</span>} />
              <FieldRow label="Customer ID" value={<span style={{ color: '#8b949e' }}>{subscription?.customer_id ? maskId(subscription.customer_id) : 'Not configured'}</span>} note="Masked for security" />
              <FieldRow label="Subscription Status" value={<StatusPill status={billingAvailable ? billingStatusDisplay : 'Not Configured'} />} />
              <FieldRow label="Invoice Status" value={<StatusPill status={subscription?.invoice_status ? String(subscription.invoice_status) : 'Not Configured'} />} />
              {!billingAvailable ? (
                <p style={{ marginTop: '0.75rem', color: '#8b949e', fontSize: '0.82rem' }}>{billingDisabledMessage(billingRuntime)}</p>
              ) : null}
              <div style={{ marginTop: '0.85rem' }}>
                {billingAvailable && plans.length > 0 ? (
                  <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
                    {plans.map((plan) => (
                      <button key={plan.plan_key} className="btn btn-primary" type="button" onClick={() => void startCheckout(plan.plan_key)}>
                        {plan.plan_name}
                      </button>
                    ))}
                  </div>
                ) : (
                  <button className="btn btn-secondary" type="button" disabled style={{ opacity: 0.5, cursor: 'not-allowed' }}>
                    Configure Billing -Action not configured
                  </button>
                )}
              </div>
            </SectionCard>

          </div>
        </section>
      ) : null}

      
      {activeTab === 'notifications' ? (
        <section className="featureSection">
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: '1rem', marginTop: '1rem' }}>
            {/* Alert Notifications */}
            <SectionCard title="Alert Notifications">
              <FieldRow label="Critical Alerts" value={<StatusPill status="Not Configured" />} />
              <FieldRow label="High Alerts" value={<StatusPill status="Not Configured" />} />
              <FieldRow label="Medium Alerts" value={<StatusPill status="Not Configured" />} />
              <FieldRow label="Digest Frequency" value={<span style={{ color: '#8b949e' }}>Not configured</span>} />
            </SectionCard>
            {/* Incident Notifications */}
            <SectionCard title="Incident Notifications">
              <FieldRow label="Incident Opened" value={<StatusPill status="Not Configured" />} />
              <FieldRow label="Response Required" value={<StatusPill status="Not Configured" />} />
              <FieldRow label="Incident Resolved" value={<StatusPill status="Not Configured" />} />
            </SectionCard>
            {/* Evidence Notifications */}
            <SectionCard title="Evidence Notifications">
              <FieldRow label="Package Created" value={<StatusPill status="Not Configured" />} />
              <FieldRow label="Export Completed" value={<StatusPill status="Not Configured" />} />
              <FieldRow label="Export Failed" value={<StatusPill status="Not Configured" />} />
            </SectionCard>
          </div>
          {/* Channels table */}
          <article className="dataCard" style={{ marginTop: '1rem' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.85rem' }}>
              <p className="sectionEyebrow" style={{ margin: 0 }}>Notification Channels</p>
              <Link className="btn btn-secondary" href="/integrations" prefetch={false} style={{ fontSize: '0.8rem', textDecoration: 'none' }}>
                Manage via Integrations
              </Link>
            </div>
            <DataTable headers={CHANNEL_HEADERS}>
              <tr>
                <td>
                  <span style={{ fontWeight: 600 }}>Email</span>
                  <span className="tableMeta">{user?.email ?? 'Not configured'}</span>
                </td>
                <td><span className="pill pill-neutral">Email</span></td>
                <td><StatusPill status="Not Configured" /></td>
                <td><span className="muted">-</span></td>
                <td>
                  <button className="btn btn-ghost" type="button" disabled style={{ fontSize: '0.78rem', padding: '0.25rem 0.6rem', opacity: 0.5 }}>
                    Action not configured
                  </button>
                </td>
              </tr>
              <tr>
                <td>
                  <span style={{ fontWeight: 600 }}>Slack</span>
                  <span className="tableMeta">Workspace channel</span>
                </td>
                <td><span className="pill pill-neutral">Slack</span></td>
                <td><StatusPill status="Not Configured" /></td>
                <td><span className="muted">-</span></td>
                <td>
                  <Link className="btn btn-ghost" href="/integrations" prefetch={false} style={{ fontSize: '0.78rem', padding: '0.25rem 0.6rem', textDecoration: 'none' }}>
                    Configure
                  </Link>
                </td>
              </tr>
              <tr>
                <td>
                  <span style={{ fontWeight: 600 }}>Webhook</span>
                  <span className="tableMeta">Custom endpoint</span>
                </td>
                <td><span className="pill pill-neutral">Webhook</span></td>
                <td><StatusPill status="Not Configured" /></td>
                <td><span className="muted">-</span></td>
                <td>
                  <Link className="btn btn-ghost" href="/integrations" prefetch={false} style={{ fontSize: '0.78rem', padding: '0.25rem 0.6rem', textDecoration: 'none' }}>
                    Configure
                  </Link>
                </td>
              </tr>
              <tr>
                <td>
                  <span style={{ fontWeight: 600 }}>PagerDuty</span>
                  <span className="tableMeta">On-call routing</span>
                </td>
                <td><span className="pill pill-neutral">PagerDuty</span></td>
                <td><StatusPill status="Not Configured" /></td>
                <td><span className="muted">-</span></td>
                <td>
                  <button className="btn btn-ghost" type="button" disabled style={{ fontSize: '0.78rem', padding: '0.25rem 0.6rem', opacity: 0.5 }}>
                    Action not configured
                  </button>
                </td>
              </tr>
            </DataTable>
            <p className="muted" style={{ marginTop: '0.75rem', fontSize: '0.8rem' }}>
              Add email, webhook, or Slack channels via{' '}
              <Link href="/integrations" prefetch={false} style={{ color: '#6aa9ff' }}>Integrations</Link>.
              No channels are active until configured and verified.
            </p>
          </article>

        </section>
      ) : null}

      {error ? <p style={{ marginTop: '1rem', color: '#f87171', fontSize: '0.82rem' }}>{error}</p> : null}
    </main>
  );
}




