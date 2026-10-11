'use client';

import Link from 'next/link';
import type { ReactNode } from 'react';

import { AnimatedNumber } from '../../components/motion';
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
    <div className="msrcStatRow">
      <span className="msrcStatLabel">{label}</span>
      <span className="msrcStatValue">{value}</span>
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
  diagnosticBusy,
  onToggleAutoRouting,
  onRunHealthCheck,
  onRunDiagnostic,
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
  diagnosticBusy: boolean;
  onToggleAutoRouting: () => void;
  onRunHealthCheck: () => void;
  onRunDiagnostic: () => void;
  onOpenDecision: (decision: AgentDecision) => void;
  aiUnavailable?: boolean;
}) {
  const healthyCount = providerHealth?.healthy_count ?? 0;
  const degraded = providerHealth?.degraded_count ?? 0;
  const unknown = providerHealth?.unknown_count ?? 0;
  const overallPct = summary?.source_health.health_pct ?? null;
  const autoRouting = settings?.auto_routing_enabled ?? false;

  return (
    <div className="msrcAgent">
      {/* ── Provider Health ─────────────────────────────────── */}
      <article className="dataCard msrcAgentCard msrcAgentCard--lead">
        <div className="msrcAgentHead">
          <p className="sectionEyebrow msrcAgentEyebrow msrcIcoAgent">Source Optimization Agent</p>
          <StatusPill
            label={agent ? agent.state.replace(/_/g, ' ') : '—'}
            variant={agent?.state === 'monitoring' ? 'success' : agent?.state === 'attention_required' ? 'warning' : 'neutral'}
          />
        </div>
        <p className="msrcAgentNote">
          Deterministic health rules decide routing. The AI layer only explains decisions and never invents metrics.
        </p>

        <div className="msrcAgentStats">
          <div className="msrcAgentStat">
            <div className="msrcAgentStatValue">{overallPct == null ? '—' : <AnimatedNumber value={overallPct} format={(n) => `${n.toFixed(0)}%`} />}</div>
            <div className="msrcAgentStatLabel">Ingestion health</div>
          </div>
          <div className="msrcAgentStat">
            {/* A zero count is never tinted green: no healthy provider is not a healthy signal. */}
            <div className={`msrcAgentStatValue${healthyCount > 0 ? ' msrcTone--success' : ' msrcTone--muted'}`}><AnimatedNumber value={healthyCount} /></div>
            <div className="msrcAgentStatLabel">Healthy</div>
          </div>
          <div className="msrcAgentStat">
            <div className={`msrcAgentStatValue${degraded ? ' msrcTone--danger' : ' msrcTone--muted'}`}><AnimatedNumber value={degraded} /></div>
            <div className="msrcAgentStatLabel">Degraded</div>
          </div>
          <div className="msrcAgentStat">
            <div className="msrcAgentStatValue msrcTone--muted"><AnimatedNumber value={unknown} /></div>
            <div className="msrcAgentStatLabel">Unknown</div>
          </div>
        </div>
      </article>

      {/* ── Current Agent Assessment ────────────────────────── */}
      <article className="dataCard msrcAgentCard">
        <p className="sectionEyebrow msrcAgentEyebrow">Current Agent Assessment</p>
        {loading ? (
          <p className="msrcAgentEmpty">Reading canonical monitoring state…</p>
        ) : aiUnavailable ? (
          <p className="msrcAgentWarn">
            AI explanation is unavailable. Deterministic monitoring remains active.
          </p>
        ) : !agent ? (
          <p className="msrcAgentEmpty">Agent state unavailable.</p>
        ) : (
          <>
            <p className="msrcAgentText">
              {agent.confidence_basis || 'No routing change is required based on measured records.'}
            </p>
            <div className="msrcStatList">
              <StatRow label="Healthy providers" value={agent.healthy_providers} />
              <StatRow label="Degraded providers" value={agent.degraded_providers} />
              <StatRow label="Missing target links" value={agent.missing_target_links} />
              <StatRow label="Confidence" value={<StatusPill label={agent.confidence} variant={confidenceVariant(agent.confidence)} />} />
            </div>
            {agent.latest_routing_decision ? (
              <p className="msrcAgentFoot">
                Latest routing note: {agent.latest_routing_decision}
              </p>
            ) : null}
          </>
        )}
      </article>

      {/* ── AI Recommendations ─────────────────────────────── */}
      <article className="dataCard msrcAgentCard">
        <p className="sectionEyebrow msrcAgentEyebrow">AI Recommendations</p>
        {agent && agent.recommendations.length > 0 ? (
          <ul className="msrcRecList">
            {agent.recommendations.map((rec, index) => (
              <li key={`${rec.kind}-${index}`} className="msrcRec">{rec.detail}</li>
            ))}
          </ul>
        ) : (
          <p className="msrcAgentEmpty">
            No linkage or routing issues detected in canonical records.
          </p>
        )}
        <div className="msrcAgentActions">
          <button
            type="button"
            className="btn btn-primary msrcAgentBtn"
            disabled={diagnosticBusy}
            title="Poll the configured RPC provider now and record real runtime evidence"
            onClick={onRunDiagnostic}
          >
            {diagnosticBusy ? 'Diagnosing…' : 'Run Diagnostic'}
          </button>
          <button
            type="button"
            className="btn btn-secondary msrcAgentBtn"
            disabled={healthCheckBusy}
            title="Evaluate current source health from recorded evidence"
            onClick={onRunHealthCheck}
          >
            {healthCheckBusy ? 'Evaluating…' : 'Evaluate Health'}
          </button>
          <Link href="/integrations" prefetch={false} className="btn btn-secondary msrcAgentBtn">
            Add fallback provider
          </Link>
        </div>
      </article>

      {/* ── Auto-Routing ───────────────────────────────────── */}
      <article className="dataCard msrcAgentCard">
        <div className="msrcAgentHead">
          <p className="sectionEyebrow msrcAgentEyebrow">Auto-Routing</p>
          <button
            type="button"
            role="switch"
            aria-checked={autoRouting}
            aria-label="Toggle Auto-Routing"
            disabled={autoRoutingBusy || !settings}
            onClick={onToggleAutoRouting}
            className="msrcSwitch"
            data-busy={autoRoutingBusy ? 'true' : undefined}
          >
            <span className="msrcSwitchKnob" aria-hidden />
          </button>
        </div>
        <div className="msrcStatList">
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
        </div>
        {/* Only once canonical agent state exists: with no agent state this
            panel must not assert that primary monitoring is active. */}
        {agent && !agent.recommended_fallback ? (
          <p className="msrcAgentCallout">
            Primary monitoring is active, but no approved fallback provider is available.
          </p>
        ) : null}
      </article>

      {/* ── Recent Agent Decisions ─────────────────────────── */}
      <article className="dataCard msrcAgentCard">
        <p className="sectionEyebrow msrcAgentEyebrow">Recent Agent Decisions</p>
        {decisions.length === 0 ? (
          <p className="msrcAgentEmpty">
            No agent decisions recorded yet. Run a health check to evaluate source health.
          </p>
        ) : (
          <ul className="msrcDecisionList">
            {decisions.slice(0, 8).map((decision) => {
              const badge = decisionBadge(decision.decision_type);
              return (
                <li key={decision.id}>
                  <button
                    type="button"
                    onClick={() => onOpenDecision(decision)}
                    className={`msrcDecision msrcDecision--${badge.variant}`}
                  >
                    <span className="msrcDecisionHead">
                      <StatusPill label={badge.label} variant={badge.variant} />
                      {decision.approval_required ? <StatusPill label="Approval required" variant="warning" /> : null}
                    </span>
                    <span className="msrcDecisionSummary">{decision.summary || '—'}</span>
                    <span className="msrcDecisionTime">{fmtRelative(decision.created_at)}</span>
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
