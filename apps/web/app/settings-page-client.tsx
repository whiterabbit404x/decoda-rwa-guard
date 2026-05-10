'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';
import type { ReactNode } from 'react';

import { usePilotAuth } from 'app/pilot-auth-context';
import { BillingRuntime, billingDisabledMessage, billingEnabled } from './billing-capability';

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
  const { apiUrl, authHeaders, error, liveModeConfigured, loading, user } = usePilotAuth();
  const [members, setMembers] = useState<Member[]>([]);
  const [invitations, setInvitations] = useState<Invitation[]>([]);
  const [plans, setPlans] = useState<Array<{ plan_key: string; plan_name: string; max_members: number }>>([]);
  const [subscription, setSubscription] = useState<any>(null);
  const [billingRuntime, setBillingRuntime] = useState<BillingRuntime>({ provider: 'none', available: false });
  const [seatSummary, setSeatSummary] = useState<SeatSummary | null>(null);
  const [inviteRole, setInviteRole] = useState('viewer');
  const [message, setMessage] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [activeTab, setActiveTab] = useState<TabKey>('general');
  const [wsName, setWsName] = useState('');
  const [timezone, setTimezone] = useState('UTC');
  const [currency, setCurrency] = useState('USD');

  const fallbackWorkspace = user?.memberships?.[0]?.workspace ?? null;
  const resolvedWorkspace = user?.current_workspace ?? fallbackWorkspace;
  const hasWorkspace = Boolean(resolvedWorkspace?.id);
  useEffect(() => {
    if (resolvedWorkspace?.name) setWsName(resolvedWorkspace.name);
  }, [resolvedWorkspace?.name]);

  async function call(path: string, init?: RequestInit) {
    return fetch(`${apiUrl}${path}`, { cache: 'no-store', ...init, headers: { ...(init?.headers ?? {}), ...authHeaders() } });

  async function loadAll() {
    if (!apiUrl || !resolvedWorkspace?.id) return;
    const [membersRes, inviteRes, seatsRes, subscriptionRes, plansRes, readinessRes] = await Promise.all([
      call('/workspace/members'),
      call('/workspace/invitations'),
      call('/team/seats'),
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
  }

  useEffect(() => { void loadAll(); }, [apiUrl, resolvedWorkspace?.id]);
  async function inviteMember() {
    if (!apiUrl || !inviteEmail) return;
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
      {/* 鈹€鈹€ Page header 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€ */}
      <section className="featureSection">
        <div className="sectionHeader">
          <div>
            <p className="eyebrow">Settings</p>
            <h1 style={{ margin: '0.25rem 0 0.5rem' }}>Settings</h1>
            <p className="lede">Manage workspace, team, security, billing, and notification preferences.</p>
          </div>
        </div>

        {/* 鈹€鈹€ Top metric cards 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€ */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: '1rem', marginTop: '1.25rem' }}>
          <MetricCard label="Workspace Status" value={<StatusPill status={workspaceStatus} />} sub={resolvedWorkspace?.name ?? 'No workspace'} />
          <MetricCard label="Team Members" value={teamCount} sub={`${members.length} active 路 ${invitations.length} invited`} />
          <MetricCard label="Security Posture" value={<StatusPill status={securityPosture} />} sub={readiness ? `${readiness.blocking_failures?.length ?? 0} blocking issues` : 'Not evaluated'} />
          <MetricCard label="Billing Status" value={<StatusPill status={billingStatusDisplay} />} sub={billingAvailable ? `Provider: ${billingRuntime.provider ?? 'unknown'}` : 'Not configured'} />
        </div>

        {/* 鈹€鈹€ Tabs 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€ */}
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

      {/* 鈹€鈹€ General tab 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€ */}
      {activeTab === 'general' ? (
        <section className="featureSection">
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(340px, 1fr))', gap: '1rem', marginTop: '1rem' }}>

            {/* Workspace Profile */}
            <SectionCard title="Workspace Profile">
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
                      style={{ width: '100%', background: '#0d1117', border: '1px solid #30363d', borderRadius: 8, color: '#e6edf3', padding: '0.45rem 0.65rem', fontSize: '0.85rem' }}
                    />
                  } />
                  <FieldRow label="Workspace ID" readOnly value={<code style={{ fontSize: '0.8rem', color: '#8b949e' }}>{resolvedWorkspace?.id ?? '-'}</code>} />
                  <FieldRow label="Organization" value={<span style={{ color: '#8b949e' }}>{resolvedWorkspace?.name ?? '-'}</span>} />
                  <FieldRow label="Primary Contact" value={user?.full_name ?? user?.email ?? '-'} />
                  <FieldRow label="Support Email" value={<a href="mailto:support@decoda.app" style={{ color: '#6aa9ff', textDecoration: 'none' }}>support@decoda.app</a>} />
                  <div style={{ marginTop: '0.85rem' }}>
                    <button className="btn btn-secondary" type="button" disabled style={{ opacity: 0.5, cursor: 'not-allowed' }}>
                      Save Changes 鈥?Action not configured
                    </button>
                  </div>
                </>
              )}
            </SectionCard>

            {/* Workspace Defaults */}
            <SectionCard title="Workspace Defaults">
              <FieldRow label="Timezone" value={
                <select
                  value={timezone}
                  onChange={(e) => setTimezone(e.target.value)}
                  style={{ background: '#0d1117', border: '1px solid #30363d', borderRadius: 8, color: '#e6edf3', padding: '0.4rem 0.6rem', fontSize: '0.85rem', width: '100%' }}
                >
                  <option value="UTC">UTC</option>
                  <option value="America/New_York">America/New_York</option>
                  <option value="America/Los_Angeles">America/Los_Angeles</option>
                  <option value="Europe/London">Europe/London</option>
                  <option value="Europe/Berlin">Europe/Berlin</option>
                  <option value="Asia/Singapore">Asia/Singapore</option>
                  <option value="Asia/Tokyo">Asia/Tokyo</option>
                </select>
              } />
              <FieldRow label="Currency" value={
                <select
                  value={currency}
                  onChange={(e) => setCurrency(e.target.value)}
                  style={{ background: '#0d1117', border: '1px solid #30363d', borderRadius: 8, color: '#e6edf3', padding: '0.4rem 0.6rem', fontSize: '0.85rem', width: '100%' }}
                >
                  <option value="USD">USD</option>
                  <option value="EUR">EUR</option>
                  <option value="GBP">GBP</option>
                  <option value="SGD">SGD</option>
                  <option value="JPY">JPY</option>
                </select>
              } />
              <FieldRow label="Default Evidence Retention" value={<span style={{ color: '#8b949e' }}>90 days</span>} note="Configurable via evidence settings" />
              <FieldRow label="Default Monitoring Mode" value={<StatusPill status={liveModeConfigured ? 'Live' : 'Sample'} />} note={liveModeConfigured ? 'Connected to live data sources' : 'Using sample data only'} />
              <FieldRow label="API Diagnostics" value={<span style={{ color: '#8b949e', fontSize: '0.8rem' }}>{apiUrl || 'Not configured'}</span>} />
              <div style={{ marginTop: '0.85rem' }}>
                <button className="btn btn-secondary" type="button" disabled style={{ opacity: 0.5, cursor: 'not-allowed' }}>
                  Save Defaults 鈥?Action not configured
                </button>
              </div>
            </SectionCard>

          </div>
        </section>
      ) : null}
      {/* 鈹€鈹€ Team tab 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€ */}
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
                    onChange={(e) => setInviteEmail(e.target.value)}
                    placeholder="teammate@company.com"
                    style={{ width: '100%', background: '#0d1117', border: '1px solid #30363d', borderRadius: 8, color: '#e6edf3', padding: '0.5rem 0.7rem', fontSize: '0.85rem' }}
                  />
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
                  disabled={!apiUrl}
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

      {/* 鈹€鈹€ Security tab 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€ */}
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

      {/* 鈹€鈹€ Billing tab 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€ */}
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
                <button className="btn btn-secondary" type="button" disabled style={{ opacity: 0.5, cursor: 'not-allowed' }}>
                  Manage Plan 鈥?Action not configured
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
            {/* Billing Readiness */}
            <SectionCard title="Billing Readiness">
              <FieldRow label="Billing Enabled" value={<StatusPill status={billingAvailable ? 'Enabled' : 'Not Configured'} />} />
              <FieldRow label="Payment Provider" value={<span style={{ color: '#8b949e' }}>{billingRuntime.provider && billingRuntime.provider !== 'none' ? billingRuntime.provider : 'Not configured'}</span>} />
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
                    Configure Billing 鈥?Action not configured
                  </button>
                )}
              </div>
            </SectionCard>

          </div>
        </section>
      ) : null}

      {/* 鈹€鈹€ Notifications tab 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€ */}
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
