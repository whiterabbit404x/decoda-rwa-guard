'use client';

import Link from 'next/link';
import type { ReactNode } from 'react';

import { StatusPill } from '../components/ui-primitives';
import {
  executionModeLabel,
  fmtExact,
  fmtRelative,
  riskVariant,
  severityVariant,
  type ControlPlane,
  type Recommendation,
} from './integration-gateway-types';

function StatRow({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: '0.5rem', padding: '0.18rem 0' }}>
      <span className="muted" style={{ fontSize: '0.75rem' }}>{label}</span>
      <span style={{ fontSize: '0.8rem', fontWeight: 600, textAlign: 'right' }}>{value}</span>
    </div>
  );
}

const RISK_COLOR: Record<string, string> = {
  low: 'var(--success-fg, #16a34a)',
  medium: 'var(--warning-fg, #d97706)',
  high: 'var(--danger-fg, #dc2626)',
  critical: 'var(--danger-fg, #dc2626)',
  unknown: 'var(--text-muted, #94a3b8)',
};

function agentStateVariant(state: string): 'success' | 'warning' | 'danger' | 'neutral' {
  switch ((state || '').toLowerCase()) {
    case 'healthy': return 'success';
    case 'attention_required': return 'danger';
    case 'monitoring': return 'warning';
    default: return 'neutral';
  }
}

function RecommendationCard({
  rec,
  canManage,
  busy,
  onAcknowledge,
  onDismiss,
}: {
  rec: Recommendation;
  canManage: boolean;
  busy: boolean;
  onAcknowledge: (rec: Recommendation) => void;
  onDismiss: (rec: Recommendation) => void;
}) {
  const needsApproval = rec.execution_mode === 'approval_required';
  return (
    <li
      style={{
        background: 'var(--surface-subtle, rgba(148,163,184,0.06))',
        border: '1px solid var(--border-subtle, rgba(148,163,184,0.15))',
        borderRadius: 6, padding: '0.55rem 0.6rem', display: 'flex', flexDirection: 'column', gap: '0.3rem',
      }}
    >
      <span style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '0.4rem', flexWrap: 'wrap' }}>
        <StatusPill label={rec.severity} variant={severityVariant(rec.severity)} />
        <StatusPill
          label={executionModeLabel(rec.execution_mode)}
          variant={needsApproval ? 'warning' : rec.execution_mode === 'manual' ? 'info' : 'neutral'}
        />
      </span>
      <span style={{ fontSize: '0.8rem', fontWeight: 600 }}>{rec.title}</span>
      {rec.reason ? (
        <span className="muted" style={{ fontSize: '0.72rem' }}>{rec.reason}</span>
      ) : null}
      {rec.risk_if_ignored ? (
        <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>Risk if ignored: {rec.risk_if_ignored}</span>
      ) : null}
      <span className="muted" style={{ fontSize: '0.66rem' }}>
        {rec.status === 'acknowledged' ? 'Acknowledged · ' : ''}
        Detected {rec.detected_count}× · updated {fmtRelative(rec.updated_at)}
      </span>
      <div style={{ display: 'flex', gap: '0.35rem', flexWrap: 'wrap', marginTop: '0.1rem' }}>
        {needsApproval ? (
          // The actual failover switch runs through the canonical Screen-4 routing +
          // Screen-8 approval flow (§16/§19) — we link there, never mutate here.
          <Link
            href="/monitoring-sources"
            prefetch={false}
            className="btn btn-secondary"
            style={{ fontSize: '0.7rem', padding: '0.18rem 0.5rem' }}
          >
            Review in routing
          </Link>
        ) : null}
        {canManage ? (
          <>
            {rec.status !== 'acknowledged' ? (
              <button
                type="button"
                className="btn btn-secondary"
                style={{ fontSize: '0.7rem', padding: '0.18rem 0.5rem' }}
                disabled={busy}
                onClick={() => onAcknowledge(rec)}
              >
                Acknowledge
              </button>
            ) : null}
            <button
              type="button"
              className="btn btn-secondary"
              style={{ fontSize: '0.7rem', padding: '0.18rem 0.5rem' }}
              disabled={busy}
              onClick={() => onDismiss(rec)}
            >
              Dismiss
            </button>
          </>
        ) : null}
      </div>
    </li>
  );
}

export function IntegrationGatewayAgentPanel({
  data,
  loading,
  healthCheckBusy,
  recBusyId,
  onRunHealthCheck,
  onAcknowledge,
  onDismiss,
}: {
  data: ControlPlane | null;
  loading: boolean;
  healthCheckBusy: boolean;
  recBusyId: string | null;
  onRunHealthCheck: () => void;
  onAcknowledge: (rec: Recommendation) => void;
  onDismiss: (rec: Recommendation) => void;
}) {
  const agent = data?.agent ?? null;
  const risk = data?.connection_risk ?? null;
  const recommendations = data?.recommendations ?? [];
  const lastScan = data?.last_scan ?? null;
  const canManage = Boolean(data?.permissions.can_manage);
  const riskLevel = (risk?.level ?? 'unknown').toLowerCase();

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
      {/* ── Agent header ─────────────────────────────────────── */}
      <article className="dataCard" style={{ padding: '1rem' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.4rem' }}>
          <p className="sectionEyebrow" style={{ margin: 0 }}>Integration Gateway Agent</p>
          <StatusPill
            label={agent ? agent.state.replace(/_/g, ' ') : '—'}
            variant={agentStateVariant(agent?.state ?? '')}
          />
        </div>
        <p className="muted" style={{ margin: 0, fontSize: '0.73rem' }}>
          Deterministic detectors evaluate external dependency health from canonical evidence.
          The AI layer only explains and prioritises — it never invents provider health.
        </p>
      </article>

      {/* ── Connection Risk (derived, never hard-coded §14) ──── */}
      <article className="dataCard" style={{ padding: '1rem' }}>
        <p className="sectionEyebrow" style={{ margin: '0 0 0.4rem' }}>Connection Risk</p>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem' }}>
          <span style={{ fontSize: '1.5rem', fontWeight: 800, color: RISK_COLOR[riskLevel] ?? RISK_COLOR.unknown, textTransform: 'uppercase' }}>
            {loading && !risk ? '—' : (risk?.level ?? 'unknown')}
          </span>
          <StatusPill label={risk ? risk.level : 'unknown'} variant={riskVariant(riskLevel)} />
        </div>
        {risk?.reason ? (
          <p className="muted" style={{ fontSize: '0.73rem', margin: '0.4rem 0 0' }}>{risk.reason}</p>
        ) : null}
        <div style={{ borderTop: '1px solid var(--border-subtle, rgba(148,163,184,0.2))', marginTop: '0.5rem', paddingTop: '0.4rem' }}>
          <StatRow
            label="Last scan"
            value={
              lastScan?.completed_at ? (
                <span title={fmtExact(lastScan.completed_at)}>{fmtRelative(lastScan.completed_at)}</span>
              ) : (
                <span className="muted">Never</span>
              )
            }
          />
          <StatRow label="Providers checked" value={lastScan ? lastScan.checked_count : '—'} />
          <StatRow label="Open recommendations" value={agent ? agent.open_recommendations : '—'} />
        </div>
        <button
          type="button"
          className="btn btn-primary"
          style={{ marginTop: '0.6rem', width: '100%', fontSize: '0.78rem' }}
          disabled={healthCheckBusy || !canManage}
          title={canManage ? 'Run a real backend health check across configured integrations' : 'Requires the Manage Integrations permission'}
          onClick={onRunHealthCheck}
        >
          {healthCheckBusy ? 'Running health check…' : 'Run Health Check'}
        </button>
        {!canManage ? (
          <p className="muted" style={{ fontSize: '0.68rem', margin: '0.35rem 0 0' }}>
            View-only role: running a health check requires the Manage Integrations permission.
          </p>
        ) : null}
      </article>

      {/* ── AI Recommendations (structured, evidence-backed §12) ── */}
      <article className="dataCard" style={{ padding: '1rem' }}>
        <p className="sectionEyebrow" style={{ margin: '0 0 0.5rem' }}>AI Recommendations</p>
        {loading && recommendations.length === 0 ? (
          <p className="muted" style={{ fontSize: '0.76rem', margin: 0 }}>Reading canonical integration state…</p>
        ) : recommendations.length === 0 ? (
          <p className="muted" style={{ fontSize: '0.76rem', margin: 0 }}>
            No integration risks detected. The Integration Gateway Agent has no remediation
            recommendations at this time.
          </p>
        ) : (
          <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
            {recommendations.map((rec) => (
              <RecommendationCard
                key={rec.id}
                rec={rec}
                canManage={canManage}
                busy={recBusyId === rec.id}
                onAcknowledge={onAcknowledge}
                onDismiss={onDismiss}
              />
            ))}
          </ul>
        )}
      </article>
    </div>
  );
}
