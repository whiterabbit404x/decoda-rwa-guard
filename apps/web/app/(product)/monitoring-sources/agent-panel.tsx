'use client';

import Link from 'next/link';
import type { ReactNode } from 'react';

import { StatusPill, type PillVariant } from '../../components/ui-primitives';
import {
  fmtRelative,
  type AgentDecision,
  type AgentState,
  type ProviderHealthSummary,
  type SourceSettings,
  type SourceSummary,
} from './source-types';

function confidenceVariant(confidence: string): PillVariant {
  switch ((confidence || '').toLowerCase()) {
    case 'high': return 'success';
    case 'medium': return 'warning';
    case 'low': return 'danger';
    default: return 'neutral';
  }
}

function StatRow({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: '0.5rem', padding: '0.18rem 0' }}>
      <span className="muted" style={{ fontSize: '0.75rem' }}>{label}</span>
      <span style={{ fontSize: '0.8rem', fontWeight: 600, textAlign: 'right' }}>{value}</span>
    </div>
  );
}

const DECISION_LABELS: Record<string, { label: string; variant: PillVariant }> = {
  no_action: { label: 'No action required', variant: 'neutral' },
  health_check_completed: { label: 'Health check completed', variant: 'info' },
  warning_opened: { label: 'Degradation detected', variant: 'warning' },
  failover_recommended: { label: 'Failover recommended', variant: 'warning' },
  failover_initiated: { label: 'Failover initiated', variant: 'warning' },
  failover_completed: { label: 'Failover completed', variant: 'success' },
  failover_failed: { label: 'Failover failed', variant: 'danger' },
  route_restored: { label: 'Route restored', variant: 'success' },
  escalation_created: { label: 'Engineering escalation', variant: 'danger' },
  provider_marked_unhealthy: { label: 'Provider marked unhealthy', variant: 'danger' },
  oracle_heartbeat_warning: { label: 'Oracle heartbeat warning', variant: 'warning' },
};

function decisionBadge(type: string): { label: string; variant: PillVariant } {
  return DECISION_LABELS[type] ?? { label: type.replace(/_/g, ' '), variant: 'neutral' };
}

export function SourceOptimizationAgentPanel({
  agent,
  providerHealth,
  summary,
  settings,
  decisions,
  loading,
  autoRoutingBusy,
  healthCheckBusy,
  onToggleAutoRouting,
  onRunHealthCheck,
  onOpenDecision,
  aiUnavailable,
}: {
  agent: AgentState | null;
  providerHealth: ProviderHealthSummary | null;
  summary: SourceSummary | null;
  settings: SourceSettings | null;
  decisions: AgentDecision[];
  loading: boolean;
  autoRoutingBusy: boolean;
  healthCheckBusy: boolean;
  onToggleAutoRouting: () => void;
  onRunHealthCheck: () => void;
  onOpenDecision: (decision: AgentDecision) => void;
  aiUnavailable?: boolean;
}) {
  const healthyCount = providerHealth?.healthy_count ?? 0;
  const degraded = providerHealth?.degraded_count ?? 0;
  const unknown = providerHealth?.unknown_count ?? 0;
  const overallPct = summary?.source_health.health_pct ?? null;
  const autoRouting = settings?.auto_routing_enabled ?? false;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
      {/* ── Provider Health ─────────────────────────────────── */}
      <article className="dataCard" style={{ padding: '1rem' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.5rem' }}>
          <p className="sectionEyebrow" style={{ margin: 0 }}>Source Optimization Agent</p>
          <StatusPill
            label={agent ? agent.state.replace(/_/g, ' ') : '—'}
            variant={agent?.state === 'monitoring' ? 'success' : agent?.state === 'attention_required' ? 'warning' : 'neutral'}
          />
        </div>
        <p className="muted" style={{ margin: '0 0 0.6rem', fontSize: '0.74rem' }}>
          Deterministic health rules decide routing. The AI layer only explains decisions and never invents metrics.
        </p>

        <div style={{ display: 'flex', gap: '0.6rem', marginBottom: '0.5rem', textAlign: 'center' }}>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: '1.3rem', fontWeight: 700 }}>{overallPct == null ? '—' : `${overallPct.toFixed(0)}%`}</div>
            <div className="muted" style={{ fontSize: '0.66rem' }}>Ingestion health</div>
          </div>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: '1.3rem', fontWeight: 700, color: 'var(--success-fg)' }}>{healthyCount}</div>
            <div className="muted" style={{ fontSize: '0.66rem' }}>Healthy</div>
          </div>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: '1.3rem', fontWeight: 700, color: degraded ? 'var(--danger-fg)' : 'var(--text-muted)' }}>{degraded}</div>
            <div className="muted" style={{ fontSize: '0.66rem' }}>Degraded</div>
          </div>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: '1.3rem', fontWeight: 700, color: 'var(--text-muted)' }}>{unknown}</div>
            <div className="muted" style={{ fontSize: '0.66rem' }}>Unknown</div>
          </div>
        </div>
      </article>

      {/* ── Current Agent Assessment ────────────────────────── */}
      <article className="dataCard" style={{ padding: '1rem' }}>
        <p className="sectionEyebrow" style={{ margin: '0 0 0.4rem' }}>Current Agent Assessment</p>
        {loading ? (
          <p className="muted" style={{ fontSize: '0.8rem' }}>Reading canonical monitoring state…</p>
        ) : aiUnavailable ? (
          <p style={{ fontSize: '0.78rem', color: 'var(--warning-fg)', margin: 0 }}>
            AI explanation is unavailable. Deterministic monitoring remains active.
          </p>
        ) : !agent ? (
          <p className="muted" style={{ fontSize: '0.8rem' }}>Agent state unavailable.</p>
        ) : (
          <>
            <p style={{ fontSize: '0.8rem', margin: '0 0 0.5rem', color: 'var(--text-secondary)' }}>
              {agent.confidence_basis || 'No routing change is required based on measured records.'}
            </p>
            <div style={{ borderTop: '1px solid var(--border-subtle, rgba(148,163,184,0.2))', paddingTop: '0.4rem' }}>
              <StatRow label="Healthy providers" value={agent.healthy_providers} />
              <StatRow label="Degraded providers" value={agent.degraded_providers} />
              <StatRow label="Missing target links" value={agent.missing_target_links} />
              <StatRow label="Confidence" value={<StatusPill label={agent.confidence} variant={confidenceVariant(agent.confidence)} />} />
            </div>
            {agent.latest_routing_decision ? (
              <p className="muted" style={{ fontSize: '0.72rem', margin: '0.4rem 0 0' }}>
                Latest routing note: {agent.latest_routing_decision}
              </p>
            ) : null}
          </>
        )}
      </article>

      {/* ── AI Recommendations ─────────────────────────────── */}
      <article className="dataCard" style={{ padding: '1rem' }}>
        <p className="sectionEyebrow" style={{ margin: '0 0 0.4rem' }}>AI Recommendations</p>
        {agent && agent.recommendations.length > 0 ? (
          <ul style={{ margin: 0, paddingLeft: '1rem', fontSize: '0.76rem', color: 'var(--text-secondary)' }}>
            {agent.recommendations.map((rec, index) => (
              <li key={`${rec.kind}-${index}`} style={{ padding: '0.2rem 0' }}>{rec.detail}</li>
            ))}
          </ul>
        ) : (
          <p className="muted" style={{ fontSize: '0.76rem', margin: 0 }}>
            No linkage or routing issues detected in canonical records.
          </p>
        )}
        <div style={{ display: 'flex', gap: '0.4rem', marginTop: '0.6rem', flexWrap: 'wrap' }}>
          <button
            type="button"
            className="btn btn-primary"
            style={{ fontSize: '0.74rem', padding: '0.26rem 0.65rem' }}
            disabled={healthCheckBusy}
            onClick={onRunHealthCheck}
          >
            {healthCheckBusy ? 'Running…' : 'Run Diagnostic'}
          </button>
          <Link href="/integrations" prefetch={false} className="btn btn-secondary" style={{ fontSize: '0.74rem', padding: '0.26rem 0.65rem' }}>
            Add fallback provider
          </Link>
        </div>
      </article>

      {/* ── Auto-Routing ───────────────────────────────────── */}
      <article className="dataCard" style={{ padding: '1rem' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.5rem' }}>
          <p className="sectionEyebrow" style={{ margin: 0 }}>Auto-Routing</p>
          <button
            type="button"
            role="switch"
            aria-checked={autoRouting}
            aria-label="Toggle Auto-Routing"
            disabled={autoRoutingBusy || !settings}
            onClick={onToggleAutoRouting}
            style={{
              border: '1px solid var(--border-subtle, rgba(148,163,184,0.3))',
              background: autoRouting ? 'var(--success-fg, #16a34a)' : 'var(--surface-subtle, #1e293b)',
              width: 42, height: 22, borderRadius: 999, position: 'relative', cursor: autoRoutingBusy ? 'wait' : 'pointer',
            }}
          >
            <span style={{
              position: 'absolute', top: 2, left: autoRouting ? 22 : 2, width: 16, height: 16, borderRadius: '50%',
              background: '#fff', transition: 'left 120ms ease',
            }} />
          </button>
        </div>
        <StatRow label="Status" value={
          <StatusPill label={autoRouting ? 'Enabled' : 'Disabled'} variant={autoRouting ? 'success' : 'neutral'} />
        } />
        <StatRow label="Current primary" value={agent?.primary_provider || '—'} />
        <StatRow label="Current fallback" value={agent?.recommended_fallback || (
          <span className="muted" title="No approved fallback provider is available.">None</span>
        )} />
        <StatRow label="Failover cooldown" value={settings ? `${Math.round(settings.failover_cooldown_seconds / 60)} min` : '—'} />
        <StatRow label="Recovery period" value={settings ? `${Math.round(settings.route_recovery_seconds / 60)} min` : '—'} />
        <StatRow label="Route changes (24h)" value={summary?.active_routes.changed_24h ?? '—'} />
        {!agent?.recommended_fallback ? (
          <p className="muted" style={{ fontSize: '0.72rem', margin: '0.4rem 0 0' }}>
            Primary monitoring is active, but no approved fallback provider is available.
          </p>
        ) : null}
      </article>

      {/* ── Recent Agent Decisions ─────────────────────────── */}
      <article className="dataCard" style={{ padding: '1rem' }}>
        <p className="sectionEyebrow" style={{ margin: '0 0 0.4rem' }}>Recent Agent Decisions</p>
        {decisions.length === 0 ? (
          <p className="muted" style={{ fontSize: '0.76rem', margin: 0 }}>
            No agent decisions recorded yet. Run a health check to evaluate source health.
          </p>
        ) : (
          <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: '0.35rem' }}>
            {decisions.slice(0, 8).map((decision) => {
              const badge = decisionBadge(decision.decision_type);
              return (
                <li key={decision.id}>
                  <button
                    type="button"
                    onClick={() => onOpenDecision(decision)}
                    style={{
                      width: '100%', textAlign: 'left', background: 'var(--surface-subtle, rgba(148,163,184,0.06))',
                      border: '1px solid var(--border-subtle, rgba(148,163,184,0.15))', borderRadius: 6,
                      padding: '0.4rem 0.55rem', cursor: 'pointer', display: 'flex', flexDirection: 'column', gap: '0.2rem',
                    }}
                  >
                    <span style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '0.4rem' }}>
                      <StatusPill label={badge.label} variant={badge.variant} />
                      {decision.approval_required ? <StatusPill label="Approval required" variant="warning" /> : null}
                    </span>
                    <span style={{ fontSize: '0.72rem', color: 'var(--text-secondary)' }}>{decision.summary || '—'}</span>
                    <span className="muted" style={{ fontSize: '0.66rem' }}>{fmtRelative(decision.created_at)}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </article>
    </div>
  );
}
