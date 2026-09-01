'use client';

/**
 * Screen 11 — Settings ▸ Policies (Governance & Policy).
 *
 * Renders one governance policy and lets an operator evaluate a hypothetical
 * operation against it:
 *
 *     Operational event
 *         -> deterministic policy evaluation (BACKEND)
 *         -> ALLOW / DENY
 *         -> human-readable explanation
 *         -> Screen 8 response authorization gate
 *
 * What this component does NOT do
 * -------------------------------
 * It does not decide anything. `decision`, `reason_codes`, `policy_version`,
 * `required_approvals` and every check status are rendered exactly as the
 * backend's deterministic engine produced them; there is no branch here that
 * constructs a verdict, and no fallback that assumes ALLOW. Run Simulation
 * issues ONE read-only POST to the policy simulate endpoint — it never calls a
 * response/action, incident, or execution endpoint.
 *
 * Every truthfulness decision (an incomplete evaluation is "Evaluation
 * unavailable", a missing status is "Unknown" rather than "Active", an empty
 * history is an empty state rather than an invented trail) lives in
 * governance-policy-view-model.ts so it is unit-testable without React.
 */

import { useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';

import { GovernanceDialog } from './components/governance-dialog';
import type { LoadState } from './governance-view-model';
import { loadStateFor } from './governance-view-model';
import {
  GovernancePolicy,
  PolicyEvaluation,
  PolicyListState,
  PolicyVersionRow,
  SimulationState,
  allowedWindowLabel,
  businessEventLabel,
  checkGlyph,
  checkStatusLabel,
  checkStatusTone,
  defaultPolicyKey,
  formatDecimalString,
  isDemoSeeded,
  maximumIssuanceLabel,
  operationLabel,
  policyListMessage,
  policyStatusLabel,
  policyStatusTone,
  reasonCodeLabel,
  requiredRolesLabel,
  roleLabel,
  settlementRequirementLabel,
  simulationDisplay,
  simulationFailureState,
} from './governance-policy-view-model';

type Call = (path: string, init?: RequestInit) => Promise<Response>;

type Member = { user_id: string; email: string; full_name: string };

const INPUT_STYLE: React.CSSProperties = {
  width: '100%',
  background: '#0d1117',
  border: '1px solid #30363d',
  borderRadius: 8,
  color: '#e6edf3',
  padding: '0.45rem 0.65rem',
  fontSize: '0.85rem',
};

function Pill({ tone, children }: { tone: string; children: ReactNode }) {
  return <span className={`pill pill-${tone}`}>{children}</span>;
}

function DetailRow({ label, value, note }: { label: string; value: ReactNode; note?: string }) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1.3fr', gap: '0.5rem 1rem', alignItems: 'baseline', padding: '0.5rem 0', borderBottom: '1px solid #21262d' }}>
      <span style={{ color: '#8b949e', fontSize: '0.82rem', fontWeight: 600 }}>{label}</span>
      <span style={{ fontSize: '0.85rem' }}>
        {value}
        {note ? <span style={{ display: 'block', color: '#5a6478', fontSize: '0.75rem', marginTop: '0.15rem' }}>{note}</span> : null}
      </span>
    </div>
  );
}

function Field({ label, children, htmlFor }: { label: string; children: ReactNode; htmlFor?: string }) {
  return (
    <div style={{ marginBottom: '0.7rem' }}>
      <label htmlFor={htmlFor} style={{ display: 'block', fontSize: '0.78rem', color: '#8b949e', marginBottom: '0.3rem', fontWeight: 600 }}>{label}</label>
      {children}
    </div>
  );
}

export default function SettingsPoliciesPanel({
  call,
  ensureCsrf,
  hasWorkspace,
  loading,
  members,
  currentUserId,
}: {
  call: Call;
  ensureCsrf: () => Promise<void>;
  hasWorkspace: boolean;
  loading: boolean;
  members: Member[];
  currentUserId: string | null;
}) {
  const [list, setList] = useState<PolicyListState>({ state: 'loading', policies: [], canManage: false, vocabulary: null, editPermission: '' });
  const [selectedRef, setSelectedRef] = useState<string | null>(null);

  // Simulator inputs. Nothing here is authoritative: the backend re-resolves the
  // operator's role and today's issuance total server-side and ignores any claim
  // this form could make about them.
  const [simOperation, setSimOperation] = useState('MINT');
  const [simAmount, setSimAmount] = useState('5000000');
  const [simOperator, setSimOperator] = useState('');
  const [simBusinessEvent, setSimBusinessEvent] = useState('SUBSCRIPTION');
  const [simSettlement, setSimSettlement] = useState('CLEARED');
  const [simApproval, setSimApproval] = useState<'PRESENT' | 'MISSING'>('MISSING');
  const [simulation, setSimulation] = useState<SimulationState>({ state: 'idle' });

  const [dialog, setDialog] = useState<null | 'history' | 'edit'>(null);
  const [history, setHistory] = useState<{ state: LoadState; versions: PolicyVersionRow[]; currentVersion: number | null }>({ state: 'idle', versions: [], currentVersion: null });
  const [draft, setDraft] = useState<null | {
    name: string;
    status: string;
    required_business_event: string;
    settlement_requirement: string;
    window_start: string;
    window_end: string;
    maximum_daily_amount_usd: string;
    required_roles: string[];
  }>(null);
  const [saving, setSaving] = useState(false);
  const [editMessage, setEditMessage] = useState<{ tone: 'success' | 'error' | 'conflict'; text: string } | null>(null);

  async function loadPolicies() {
    if (!hasWorkspace) {
      setList({ state: 'error', policies: [], canManage: false, vocabulary: null, editPermission: '' });
      return;
    }
    try {
      const res = await call('/workspace/governance/policies');
      if (!res.ok) {
        setList({ state: loadStateFor(res.status), policies: [], canManage: false, vocabulary: null, editPermission: '' });
        return;
      }
      const payload = await res.json();
      const policies: GovernancePolicy[] = Array.isArray(payload?.policies) ? payload.policies : [];
      setList({
        state: 'loaded',
        policies,
        canManage: Boolean(payload?.can_manage),
        vocabulary: payload?.vocabulary ?? null,
        editPermission: String(payload?.edit_permission ?? ''),
      });
      setSelectedRef((prev) => (prev && policies.some((p) => p.policy_id === prev) ? prev : defaultPolicyKey(policies)));
    } catch {
      // Transport failure: an explicit unavailable state, never a spinner and
      // never an empty list that could read as "no policies, all clear".
      setList({ state: 'error', policies: [], canManage: false, vocabulary: null, editPermission: '' });
    }
  }

  useEffect(() => {
    if (loading) return;
    void loadPolicies();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loading, hasWorkspace]);

  const policy = useMemo(
    () => list.policies.find((p) => p.policy_id === selectedRef) ?? list.policies[0] ?? null,
    [list.policies, selectedRef],
  );

  // Seed the simulator's operation from the policy being viewed, so the default
  // scenario is one the policy actually governs.
  useEffect(() => {
    if (policy?.operation) setSimOperation(policy.operation);
    setSimulation({ state: 'idle' });
  }, [policy?.policy_id, policy?.operation]);

  useEffect(() => {
    if (!simOperator && currentUserId) setSimOperator(currentUserId);
  }, [currentUserId, simOperator]);

  const vocabulary = list.vocabulary;
  const operations = vocabulary?.operations ?? [{ value: 'MINT', label: 'Mint' }];
  const businessEvents = vocabulary?.business_events ?? [];
  const settlementStates = vocabulary?.settlement_states ?? [];

  async function runSimulation() {
    if (!policy) return;
    setSimulation({ state: 'running' });
    await ensureCsrf();
    try {
      const res = await call(`/workspace/governance/policies/${encodeURIComponent(policy.policy_id)}/simulate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          operation: simOperation,
          amount_usd: simAmount,
          operator_id: simOperator || null,
          business_event: simBusinessEvent || null,
          settlement_status: simSettlement || null,
          compliance_approval: simApproval === 'PRESENT',
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        const detail = body?.detail;
        const message = typeof detail === 'string' ? detail : detail?.message;
        setSimulation(simulationFailureState(res.status, message));
        return;
      }
      const evaluation: PolicyEvaluation = await res.json();
      setSimulation({ state: 'complete', evaluation });
    } catch {
      // A failed request is never an ALLOW.
      setSimulation(simulationFailureState(0));
    }
  }

  async function openHistory() {
    if (!policy) return;
    setDialog('history');
    setHistory({ state: 'loading', versions: [], currentVersion: null });
    try {
      const res = await call(`/workspace/governance/policies/${encodeURIComponent(policy.policy_id)}/history`);
      if (!res.ok) {
        setHistory({ state: loadStateFor(res.status), versions: [], currentVersion: null });
        return;
      }
      const payload = await res.json();
      setHistory({
        state: 'loaded',
        versions: Array.isArray(payload?.versions) ? payload.versions : [],
        currentVersion: payload?.current_version ?? null,
      });
    } catch {
      setHistory({ state: 'error', versions: [], currentVersion: null });
    }
  }

  function openEdit() {
    if (!policy) return;
    setEditMessage(null);
    setDraft({
      name: policy.name ?? '',
      status: policy.status ?? '',
      required_business_event: policy.required_business_event ?? '',
      settlement_requirement: policy.settlement_requirement ?? '',
      window_start: policy.allowed_window_utc?.start ?? '',
      window_end: policy.allowed_window_utc?.end ?? '',
      maximum_daily_amount_usd: policy.maximum_daily_amount_usd ?? '',
      required_roles: [...(policy.required_roles ?? [])],
    });
    setDialog('edit');
  }

  async function savePolicy() {
    if (!policy || !draft) return;
    setSaving(true);
    setEditMessage(null);
    await ensureCsrf();
    const body: Record<string, unknown> = {
      name: draft.name,
      status: draft.status,
      required_business_event: draft.required_business_event || null,
      settlement_requirement: draft.settlement_requirement || null,
      allowed_window_utc: draft.window_start && draft.window_end
        ? { start: draft.window_start, end: draft.window_end }
        : null,
      maximum_daily_amount_usd: draft.maximum_daily_amount_usd || null,
      required_roles: draft.required_roles,
      // Optimistic concurrency: a stale editor is rejected rather than silently
      // overwriting a version someone else already published.
      expected_version: policy.version,
    };
    let res: Response;
    try {
      res = await call(`/workspace/governance/policies/${encodeURIComponent(policy.policy_id)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
    } catch {
      setSaving(false);
      setEditMessage({ tone: 'error', text: 'Could not reach the policy service. No change was applied.' });
      return;
    }
    setSaving(false);
    if (res.ok) {
      setDialog(null);
      setDraft(null);
      setEditMessage({ tone: 'success', text: 'Policy saved.' });
      // A saved edit invalidates any decision shown against the old version.
      setSimulation({ state: 'idle' });
      void loadPolicies();
      return;
    }
    if (res.status === 409) {
      setEditMessage({ tone: 'conflict', text: 'This policy changed since you opened it. Reload the current version before saving.' });
      return;
    }
    if (res.status === 401 || res.status === 403) {
      setEditMessage({ tone: 'error', text: 'You do not have permission to edit governance policies.' });
      return;
    }
    const body2 = await res.json().catch(() => ({}));
    const detail = body2?.detail;
    setEditMessage({ tone: 'error', text: (typeof detail === 'string' ? detail : detail?.message) || 'Could not save the policy. No change was applied.' });
  }

  const listMessage = policyListMessage(list);
  const display = simulationDisplay(simulation);
  const evaluation = simulation.state === 'complete' ? simulation.evaluation : null;

  if (listMessage) {
    return (
      <section className="featureSection">
        <article className="dataCard" style={{ marginTop: '1rem', padding: '2rem 1.25rem', textAlign: 'center' }}>
          <h3 style={{ marginTop: 0, fontSize: '1rem' }}>{listMessage.title}</h3>
          <p className="muted" style={{ marginBottom: 0 }}>{listMessage.message}</p>
        </article>
      </section>
    );
  }

  if (!policy) return null;

  return (
    <section className="featureSection">
      {/* A — Policy header */}
      <article className="dataCard" style={{ marginTop: '1rem' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '1rem', flexWrap: 'wrap' }}>
          <div>
            <p className="sectionEyebrow" style={{ margin: '0 0 0.3rem' }}>Policy</p>
            <h2 style={{ margin: 0, fontSize: '1.1rem' }}>
              {policy.name} <span style={{ color: '#8b949e', fontWeight: 500 }}>({policy.policy_key})</span>
            </h2>
            <div style={{ display: 'flex', gap: '1.25rem', marginTop: '0.6rem', alignItems: 'center', flexWrap: 'wrap' }}>
              <span style={{ fontSize: '0.82rem', color: '#8b949e' }}>
                Status <Pill tone={policyStatusTone(policy.status)}>{policyStatusLabel(policy.status)}</Pill>
              </span>
              <span style={{ fontSize: '0.82rem', color: '#8b949e' }}>
                Version <strong style={{ color: '#e6edf3' }}>{policy.version}</strong>
              </span>
              <span style={{ fontSize: '0.82rem', color: '#8b949e' }}>
                Operation <strong style={{ color: '#e6edf3' }}>{operationLabel(policy.operation)}</strong>
              </span>
            </div>
            {isDemoSeeded(policy) ? (
              <p style={{ margin: '0.6rem 0 0', fontSize: '0.78rem', color: '#fbbf24' }}>
                Seeded demo policy — not customer-authored configuration. Its evaluations are real; its constraints were provisioned for demonstration.
              </p>
            ) : null}
          </div>
          <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
            <button
              className="btn btn-secondary"
              type="button"
              onClick={openEdit}
              disabled={!list.canManage}
              title={list.canManage ? 'Edit this policy' : `Requires the ${list.editPermission || 'security.manage'} permission`}
              style={{ opacity: list.canManage ? 1 : 0.5, cursor: list.canManage ? 'pointer' : 'not-allowed' }}
            >
              Edit Policy
            </button>
            <button className="btn btn-ghost" type="button" onClick={() => void openHistory()}>View History</button>
          </div>
        </div>
        {list.policies.length > 1 ? (
          <div style={{ marginTop: '0.9rem' }}>
            <label htmlFor="policy-select" style={{ fontSize: '0.78rem', color: '#8b949e', fontWeight: 600, marginRight: '0.5rem' }}>Policy</label>
            <select
              id="policy-select"
              value={policy.policy_id}
              onChange={(e) => setSelectedRef(e.target.value)}
              style={{ ...INPUT_STYLE, width: 'auto', minWidth: 260 }}
            >
              {list.policies.map((p) => (
                <option key={p.policy_id} value={p.policy_id}>
                  {p.name} ({p.policy_key}) · {policyStatusLabel(p.status)}
                </option>
              ))}
            </select>
          </div>
        ) : null}
        {!list.canManage ? (
          <p className="muted" style={{ fontSize: '0.78rem', margin: '0.75rem 0 0' }}>
            You can view this policy and run simulations. Editing requires the {list.editPermission || 'security.manage'} permission, which the backend enforces independently.
          </p>
        ) : null}
      </article>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(340px, 1fr))', gap: '1rem', marginTop: '1rem' }}>
        {/* B — Policy Details */}
        <article className="dataCard">
          <p className="sectionEyebrow" style={{ margin: '0 0 0.85rem' }}>Policy Details</p>
          <DetailRow label="Required business event" value={businessEventLabel(policy.required_business_event)} />
          <DetailRow label="Settlement requirement" value={<code style={{ fontSize: '0.8rem' }}>{settlementRequirementLabel(policy.settlement_requirement)}</code>} />
          <DetailRow label="Allowed window (UTC)" value={allowedWindowLabel(policy.allowed_window_utc)} />
          <DetailRow label="Maximum issuance" value={maximumIssuanceLabel(policy.maximum_daily_amount_usd)} />
          <DetailRow label="Required roles" value={requiredRolesLabel(policy.required_roles)} />
          <DetailRow
            label="On violation"
            value={<Pill tone="danger">{policy.violation_action}</Pill>}
            note="Enforced by the deterministic policy engine, not by a model."
          />
        </article>

        {/* C — Policy Simulator */}
        <article className="dataCard">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.6rem' }}>
            <p className="sectionEyebrow" style={{ margin: 0 }}>Policy Simulator</p>
            <span className="pill pill-neutral" style={{ fontSize: '0.7rem' }}>Read-only</span>
          </div>
          <p className="muted" style={{ marginTop: 0, fontSize: '0.78rem' }}>
            Predictive evaluation against the stored policy. It authorizes nothing, executes nothing, and does not change any production counter.
          </p>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: '0 0.75rem' }}>
            <Field label="Operation" htmlFor="sim-operation">
              <select id="sim-operation" value={simOperation} onChange={(e) => setSimOperation(e.target.value)} style={INPUT_STYLE}>
                {operations.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
              </select>
            </Field>
            <Field label="Amount (USD)" htmlFor="sim-amount">
              <input id="sim-amount" inputMode="decimal" value={simAmount} onChange={(e) => setSimAmount(e.target.value)} style={INPUT_STYLE} />
            </Field>
            <Field label="Operator" htmlFor="sim-operator">
              <select id="sim-operator" value={simOperator} onChange={(e) => setSimOperator(e.target.value)} style={INPUT_STYLE}>
                <option value="">Unassigned</option>
                {members.map((m) => (
                  <option key={m.user_id} value={m.user_id}>{m.full_name || m.email}</option>
                ))}
              </select>
            </Field>
            <Field label="Business Event" htmlFor="sim-business-event">
              <select id="sim-business-event" value={simBusinessEvent} onChange={(e) => setSimBusinessEvent(e.target.value)} style={INPUT_STYLE}>
                <option value="">None</option>
                {businessEvents.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
              </select>
            </Field>
            <Field label="Settlement" htmlFor="sim-settlement">
              <select id="sim-settlement" value={simSettlement} onChange={(e) => setSimSettlement(e.target.value)} style={INPUT_STYLE}>
                <option value="">Unknown</option>
                {settlementStates.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
              </select>
            </Field>
            <Field label="Compliance Approval" htmlFor="sim-approval">
              <select id="sim-approval" value={simApproval} onChange={(e) => setSimApproval(e.target.value as 'PRESENT' | 'MISSING')} style={INPUT_STYLE}>
                <option value="PRESENT">Present</option>
                <option value="MISSING">Missing</option>
              </select>
            </Field>
          </div>

          <button
            className="btn btn-primary"
            type="button"
            onClick={() => void runSimulation()}
            disabled={simulation.state === 'running'}
            style={{ marginTop: '0.5rem' }}
          >
            {simulation.state === 'running' ? 'Running…' : 'Run Simulation'}
          </button>

          {/* Simulation Result — rendered from the backend decision only. */}
          <div style={{ marginTop: '1rem', border: '1px solid #30363d', borderRadius: 10, padding: '0.9rem' }}>
            <p className="sectionEyebrow" style={{ margin: '0 0 0.5rem' }}>Simulation Result</p>
            <p
              role="status"
              data-testid="simulation-verdict"
              style={{
                margin: 0,
                fontSize: '1.45rem',
                fontWeight: 800,
                letterSpacing: '0.04em',
                color: display.tone === 'success' ? '#4ade80' : display.tone === 'danger' ? '#f87171' : '#8b949e',
              }}
            >
              {display.tone === 'success' ? '🟢 ' : display.tone === 'danger' ? '🔴 ' : ''}{display.verdict}
            </p>
            <p className="muted" style={{ margin: '0.35rem 0 0', fontSize: '0.8rem' }}>{display.detail}</p>

            {display.reasonCodes.length ? (
              <div style={{ marginTop: '0.75rem' }}>
                <p className="sectionEyebrow" style={{ margin: '0 0 0.3rem' }}>Reason</p>
                {display.reasonCodes.map((code) => (
                  <div key={code} style={{ marginBottom: '0.4rem' }}>
                    <code data-testid="reason-code" style={{ fontSize: '0.82rem', color: display.tone === 'danger' ? '#f87171' : '#4ade80', fontWeight: 700 }}>{code}</code>
                    <span style={{ display: 'block', color: '#8b949e', fontSize: '0.76rem' }}>{reasonCodeLabel(code)}</span>
                  </div>
                ))}
              </div>
            ) : null}

            {evaluation ? (
              <>
                <div style={{ marginTop: '0.85rem', paddingTop: '0.7rem', borderTop: '1px solid #21262d', display: 'grid', gap: '0.3rem' }}>
                  <span style={{ fontSize: '0.76rem', color: '#8b949e' }}>
                    Source <strong style={{ color: '#e6edf3' }}>{evaluation.decision_authority}</strong>
                  </span>
                  <span style={{ fontSize: '0.76rem', color: '#8b949e' }}>
                    AI authority <strong style={{ color: '#e6edf3' }}>{evaluation.ai_authority}</strong>
                  </span>
                  <span style={{ fontSize: '0.76rem', color: '#8b949e' }}>
                    Evaluated against <strong style={{ color: '#e6edf3' }}>{evaluation.policy_key} v{evaluation.policy_version}</strong>
                    {' · '}<code style={{ fontSize: '0.72rem' }}>{evaluation.engine_version}</code>
                  </span>
                  <span style={{ fontSize: '0.76rem', color: '#8b949e' }}>
                    Evaluation ID <code style={{ fontSize: '0.72rem' }}>{evaluation.evaluation_id}</code>
                  </span>
                </div>

                {evaluation.required_approvals?.length ? (
                  <div style={{ marginTop: '0.75rem' }}>
                    <p className="sectionEyebrow" style={{ margin: '0 0 0.3rem' }}>Approvals still required</p>
                    <p style={{ margin: 0, fontSize: '0.82rem' }}>
                      {evaluation.required_approvals.map((r) => roleLabel(r)).join(', ')}
                    </p>
                    <p className="muted" style={{ margin: '0.2rem 0 0', fontSize: '0.74rem' }}>
                      Response execution stays gated until these are collected.
                    </p>
                  </div>
                ) : null}

                {evaluation.ai_explanation ? (
                  <div style={{ marginTop: '0.85rem', paddingTop: '0.7rem', borderTop: '1px solid #21262d' }}>
                    <p className="sectionEyebrow" style={{ margin: '0 0 0.3rem' }}>
                      AI Explanation
                      <span className="pill pill-neutral" style={{ marginLeft: '0.5rem', fontSize: '0.68rem' }}>
                        {evaluation.ai_explanation_authority ?? 'AI Analysis: Explanation only'}
                      </span>
                    </p>
                    <p style={{ margin: 0, fontSize: '0.82rem' }}>{evaluation.ai_explanation}</p>
                    {evaluation.ai_next_step ? (
                      <p className="muted" style={{ margin: '0.3rem 0 0', fontSize: '0.78rem' }}>{evaluation.ai_next_step}</p>
                    ) : null}
                  </div>
                ) : null}

                {evaluation.checks?.length ? (
                  <div style={{ marginTop: '0.85rem' }}>
                    <p className="sectionEyebrow" style={{ margin: '0 0 0.35rem' }}>Deterministic evaluation</p>
                    <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
                      {evaluation.checks.map((check) => {
                        const tone = checkStatusTone(check.status);
                        return (
                          <li key={check.key} data-testid={`check-${check.key}`} style={{ display: 'flex', gap: '0.5rem', alignItems: 'baseline', padding: '0.25rem 0' }}>
                            <span aria-hidden="true" style={{ color: tone === 'success' ? '#4ade80' : tone === 'danger' ? '#f87171' : '#5a6478' }}>
                              {checkGlyph(check.status)}
                            </span>
                            <span style={{ fontSize: '0.8rem', flex: 1 }}>
                              {check.label}
                              <span style={{ display: 'block', color: '#5a6478', fontSize: '0.74rem' }}>{check.detail}</span>
                            </span>
                            <span className={`pill pill-${tone}`} style={{ fontSize: '0.68rem' }}>{checkStatusLabel(check.status)}</span>
                          </li>
                        );
                      })}
                    </ul>
                  </div>
                ) : null}
              </>
            ) : null}
          </div>
        </article>
      </div>

      {/* D — Governance explanation */}
      <article className="dataCard" style={{ marginTop: '1rem' }}>
        <p className="sectionEyebrow" style={{ margin: '0 0 0.5rem' }}>How this decision is made</p>
        <p className="muted" style={{ margin: 0, fontSize: '0.82rem' }}>
          An operational event is evaluated by a deterministic policy engine that reads only this policy&apos;s stored
          constraints and server-resolved facts. The engine produces the ALLOW/DENY decision and its reason codes; AI may
          read that result and explain it, but may not determine it, modify a policy input, approve an action, bypass a
          quorum, or execute a response. Every simulation is read-only and is excluded from production issuance totals.
        </p>
      </article>

      {/* View History */}
      <GovernanceDialog open={dialog === 'history'} title={`Version history — ${policy.policy_key}`} onClose={() => setDialog(null)} maxWidth={820}>
        {history.state === 'loading' ? <p className="muted">Loading version history…</p>
          : history.state === 'permission_denied' ? <p className="muted">You do not have permission to view this policy&apos;s history.</p>
          : history.state === 'error' ? <p role="alert" style={{ color: '#f87171' }}>Version history unavailable.</p>
          : history.versions.length === 0 ? (
            <p className="muted">
              No version history has been recorded for this policy yet. Versions appear here once a material governance
              field changes.
            </p>
          ) : (
            <div style={{ overflowX: 'auto' }}>
              <table>
                <thead><tr><th>Version</th><th>Status</th><th>Change</th><th>Changed by</th><th>When</th></tr></thead>
                <tbody>
                  {history.versions.map((row) => (
                    <tr key={row.version}>
                      <td>
                        <strong>Version {row.version}</strong>
                        {history.currentVersion === row.version
                          ? <span className="tableMeta">Current</span>
                          : <span className="tableMeta">Superseded</span>}
                      </td>
                      <td><Pill tone={policyStatusTone(row.status)}>{policyStatusLabel(row.status)}</Pill></td>
                      <td style={{ fontSize: '0.82rem' }}>{row.change_summary || 'No change summary recorded.'}</td>
                      <td style={{ fontSize: '0.82rem' }}>{row.changed_by}</td>
                      <td><span className="muted" style={{ fontSize: '0.78rem' }}>{row.changed_at ? new Date(row.changed_at).toLocaleString() : '—'}</span></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
      </GovernanceDialog>

      {/* Edit Policy */}
      <GovernanceDialog
        open={dialog === 'edit'}
        title={`Edit policy — ${policy.policy_key}`}
        onClose={() => { setDialog(null); setDraft(null); }}
        maxWidth={700}
        footer={draft ? (
          <>
            <button className="btn btn-ghost" type="button" onClick={() => { setDialog(null); setDraft(null); }}>Cancel</button>
            <button className="btn btn-primary" type="button" disabled={saving} onClick={() => void savePolicy()}>
              {saving ? 'Saving…' : 'Save policy'}
            </button>
          </>
        ) : undefined}
      >
        {draft ? (
          <div>
            <p className="muted" style={{ marginTop: 0, fontSize: '0.82rem' }}>
              Editing a material governance field publishes a new version and appends an immutable history entry. Version {policy.version} is preserved.
            </p>
            <Field label="Policy name" htmlFor="edit-name">
              <input id="edit-name" value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} style={INPUT_STYLE} />
            </Field>
            <Field label="Status" htmlFor="edit-status">
              <select id="edit-status" value={draft.status} onChange={(e) => setDraft({ ...draft, status: e.target.value })} style={INPUT_STYLE}>
                {(vocabulary?.statuses ?? []).map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
              </select>
            </Field>
            <Field label="Required business event" htmlFor="edit-business-event">
              <select id="edit-business-event" value={draft.required_business_event} onChange={(e) => setDraft({ ...draft, required_business_event: e.target.value })} style={INPUT_STYLE}>
                <option value="">Not constrained</option>
                {businessEvents.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
              </select>
            </Field>
            <Field label="Settlement requirement" htmlFor="edit-settlement">
              <select id="edit-settlement" value={draft.settlement_requirement} onChange={(e) => setDraft({ ...draft, settlement_requirement: e.target.value })} style={INPUT_STYLE}>
                <option value="">Not constrained</option>
                {(vocabulary?.settlement_requirements ?? []).map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
              </select>
            </Field>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0 0.75rem' }}>
              <Field label="Allowed window start (UTC, HH:MM)" htmlFor="edit-window-start">
                <input id="edit-window-start" placeholder="08:00" value={draft.window_start} onChange={(e) => setDraft({ ...draft, window_start: e.target.value })} style={INPUT_STYLE} />
              </Field>
              <Field label="Allowed window end (UTC, HH:MM)" htmlFor="edit-window-end">
                <input id="edit-window-end" placeholder="18:00" value={draft.window_end} onChange={(e) => setDraft({ ...draft, window_end: e.target.value })} style={INPUT_STYLE} />
              </Field>
            </div>
            <Field label="Maximum issuance (USD / day)" htmlFor="edit-max-issuance">
              <input id="edit-max-issuance" inputMode="decimal" placeholder="Not constrained" value={draft.maximum_daily_amount_usd} onChange={(e) => setDraft({ ...draft, maximum_daily_amount_usd: e.target.value })} style={INPUT_STYLE} />
            </Field>
            <fieldset style={{ border: '1px solid #30363d', borderRadius: 8, padding: '0.6rem 0.8rem', margin: '0 0 0.7rem' }}>
              <legend style={{ fontSize: '0.78rem', color: '#8b949e', fontWeight: 600, padding: '0 0.35rem' }}>Required roles</legend>
              {(vocabulary?.governance_roles ?? []).map((role) => (
                <label key={role.value} style={{ display: 'block', fontSize: '0.82rem', padding: '0.2rem 0' }}>
                  <input
                    type="checkbox"
                    checked={draft.required_roles.includes(role.value)}
                    onChange={(e) => setDraft({
                      ...draft,
                      required_roles: e.target.checked
                        ? [...draft.required_roles, role.value]
                        : draft.required_roles.filter((r) => r !== role.value),
                    })}
                    style={{ marginRight: '0.5rem' }}
                  />
                  {role.label}
                  {role.permission ? <span className="muted" style={{ marginLeft: '0.4rem', fontSize: '0.74rem' }}>({role.permission})</span> : null}
                </label>
              ))}
            </fieldset>
            {editMessage ? (
              <p role={editMessage.tone === 'success' ? 'status' : 'alert'} style={{ fontSize: '0.82rem', color: editMessage.tone === 'success' ? '#4ade80' : editMessage.tone === 'conflict' ? '#fbbf24' : '#f87171' }}>
                {editMessage.text}
                {editMessage.tone === 'conflict' ? (
                  <button className="btn btn-ghost" type="button" style={{ marginLeft: '0.5rem', fontSize: '0.76rem', padding: '0.2rem 0.5rem' }} onClick={() => { setDialog(null); setDraft(null); void loadPolicies(); }}>Reload</button>
                ) : null}
              </p>
            ) : null}
          </div>
        ) : null}
      </GovernanceDialog>
    </section>
  );
}
