'use client';

import type { ReactNode } from 'react';

import { StatusPill, type PillVariant } from '../../components/ui-primitives';
import {
  fmtExact,
  fmtLatency,
  fmtRelative,
  redactEndpoint,
  shortAddress,
  type AgentDecision,
  type SourceRow,
} from './source-types';

function Field({ label, value, mono }: { label: string; value: ReactNode; mono?: boolean }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', gap: '0.75rem', padding: '0.25rem 0', borderBottom: '1px solid var(--border-subtle, rgba(148,163,184,0.12))' }}>
      <span className="muted" style={{ fontSize: '0.74rem' }}>{label}</span>
      <span style={{ fontSize: '0.78rem', fontWeight: 600, textAlign: 'right', fontFamily: mono ? 'var(--font-mono, monospace)' : undefined }}>{value}</span>
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section style={{ marginBottom: '1rem' }}>
      <p className="sectionEyebrow" style={{ margin: '0 0 0.35rem', fontSize: '0.7rem' }}>{title}</p>
      {children}
    </section>
  );
}

function healthVariant(status?: string | null): PillVariant {
  switch ((status || '').toLowerCase()) {
    case 'healthy': return 'success';
    case 'warning': return 'warning';
    case 'critical': return 'danger';
    default: return 'neutral';
  }
}

// Metric rows that require a live probe worker. Shown honestly as "Not measured"
// rather than a fabricated value when the backend field is absent.
const NOT_MEASURED = <span className="muted" title="Requires a live provider probe">Not measured</span>;

function Shell({ title, subtitle, onClose, children }: { title: string; subtitle?: string; onClose: () => void; children: ReactNode }) {
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={title}
      style={{ position: 'fixed', inset: 0, zIndex: 60, display: 'flex', justifyContent: 'flex-end' }}
    >
      <button
        type="button"
        aria-label="Close drawer"
        onClick={onClose}
        style={{ position: 'absolute', inset: 0, background: 'rgba(2,6,23,0.6)', border: 'none', cursor: 'pointer' }}
      />
      <div
        style={{
          position: 'relative', width: 'min(480px, 100%)', height: '100%', overflowY: 'auto',
          background: 'var(--surface, #0b1220)', borderLeft: '1px solid var(--border-strong, rgba(59,130,246,0.35))',
          padding: '1.1rem 1.25rem', boxShadow: '-8px 0 32px rgba(0,0,0,0.4)',
        }}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '0.4rem' }}>
          <div>
            <h2 style={{ margin: 0, fontSize: '1.05rem', fontWeight: 700 }}>{title}</h2>
            {subtitle ? <p className="muted" style={{ margin: '0.2rem 0 0', fontSize: '0.76rem' }}>{subtitle}</p> : null}
          </div>
          <button type="button" className="btn btn-secondary" style={{ fontSize: '0.75rem', padding: '0.2rem 0.55rem' }} onClick={onClose}>
            Close
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

export function SourceDetailDrawer({
  source,
  routingHistory,
  onClose,
  onRunHealthCheck,
  healthCheckBusy,
}: {
  source: SourceRow;
  routingHistory: AgentDecision[];
  onClose: () => void;
  onRunHealthCheck: () => void;
  healthCheckBusy: boolean;
}) {
  const diagnostics = [
    'Run Connectivity Test',
    'Run RPC Method Test',
    'Compare Block Height',
    'Test Authentication',
    'Check WebSocket',
    'Validate Oracle Heartbeat',
    'View Worker Logs',
  ];

  return (
    <Shell title={source.name || 'Monitoring source'} subtitle={`${source.network || 'unknown network'} · ${source.source_type || 'source'}`} onClose={onClose}>
      <Section title="Overview">
        <Field label="Source identity" value={source.name || '—'} />
        <Field label="Endpoint (redacted)" value={redactEndpoint(source.provider || source.primary_provider)} mono />
        <Field label="Chain" value={`${source.network || '—'}${source.chain_id != null ? ` (${source.chain_id})` : ''}`} />
        <Field label="Target address" value={shortAddress(source.address)} mono />
        <Field label="Provider" value={source.provider || source.primary_provider || '—'} />
        <Field label="Source type" value={source.source_type || '—'} />
        <Field label="Routing role" value={<StatusPill label={source.routing ? source.routing : 'Unrouted'} variant={source.routing === 'primary' ? 'info' : source.routing === 'fallback' ? 'warning' : 'neutral'} />} />
        <Field
          label="Current health"
          value={
            source.health_status
              ? <StatusPill label={source.health_status} variant={healthVariant(source.health_status)} />
              : <span className="muted">No live health evidence</span>
          }
        />
      </Section>

      <Section title="Live Metrics">
        <Field label="Health score" value={source.health_score == null ? NOT_MEASURED : `${source.health_score}/100`} />
        <Field label="Latency (P95)" value={source.median_latency_ms == null ? NOT_MEASURED : fmtLatency(source.median_latency_ms)} />
        <Field label="Error rate" value={source.error_rate == null ? NOT_MEASURED : `${(source.error_rate * 100).toFixed(2)}%`} />
        <Field label="Timeout rate" value={source.timeout_rate == null ? NOT_MEASURED : `${(source.timeout_rate * 100).toFixed(2)}%`} />
        <Field label="Block lag" value={source.block_lag == null ? NOT_MEASURED : String(source.block_lag)} />
        <Field label="Provider block" value={source.latest_block == null ? '—' : `#${source.latest_block.toLocaleString()}`} />
        <Field label="Last telemetry event" value={<span title={fmtExact(source.last_telemetry_at)}>{fmtRelative(source.last_telemetry_at)}</span>} />
        <Field label="Last heartbeat" value={<span title={fmtExact(source.last_heartbeat)}>{fmtRelative(source.last_heartbeat)}</span>} />
        <Field label="Last poll" value={<span title={fmtExact(source.last_poll_at)}>{fmtRelative(source.last_poll_at)}</span>} />
        {source.triggered_rules && source.triggered_rules.length > 0 ? (
          <p className="muted" style={{ fontSize: '0.72rem', margin: '0.4rem 0 0' }}>
            Triggered rules: {source.triggered_rules.join(', ')}
          </p>
        ) : null}
      </Section>

      <Section title="Routing History">
        {routingHistory.length === 0 ? (
          <p className="muted" style={{ fontSize: '0.76rem', margin: 0 }}>No routing decisions recorded for this source.</p>
        ) : (
          <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: '0.35rem' }}>
            {routingHistory.slice(0, 6).map((decision) => (
              <li key={decision.id} style={{ fontSize: '0.74rem', color: 'var(--text-secondary)', borderLeft: '2px solid var(--border-subtle, rgba(148,163,184,0.3))', paddingLeft: '0.5rem' }}>
                <div style={{ fontWeight: 600 }}>{decision.decision_type.replace(/_/g, ' ')}</div>
                <div>
                  {decision.previous_route || '—'} → {decision.new_route || '—'} · {decision.actor_type || 'agent'} · {fmtRelative(decision.created_at)}
                </div>
                {decision.triggered_rule ? <div className="muted">Trigger: {decision.triggered_rule}</div> : null}
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title="Diagnostics">
        <p className="muted" style={{ fontSize: '0.72rem', margin: '0 0 0.5rem' }}>
          Live per-endpoint probes require the monitoring worker. Re-evaluate uses the deterministic engine over persisted evidence.
        </p>
        <button
          type="button"
          className="btn btn-primary"
          style={{ fontSize: '0.76rem', padding: '0.26rem 0.7rem', marginBottom: '0.5rem' }}
          disabled={healthCheckBusy}
          onClick={onRunHealthCheck}
        >
          {healthCheckBusy ? 'Re-evaluating…' : 'Re-evaluate source health'}
        </button>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.35rem' }}>
          {diagnostics.map((label) => (
            <button
              key={label}
              type="button"
              className="btn btn-secondary"
              style={{ fontSize: '0.72rem', padding: '0.2rem 0.5rem', opacity: 0.7 }}
              disabled
              title="Awaiting live probe worker integration"
            >
              {label}
            </button>
          ))}
        </div>
      </Section>
    </Shell>
  );
}

export function DecisionEvidenceDrawer({ decision, onClose }: { decision: AgentDecision; onClose: () => void }) {
  return (
    <Shell title={decision.decision_type.replace(/_/g, ' ')} subtitle={decision.summary || undefined} onClose={onClose}>
      <Section title="Decision">
        <Field label="Type" value={decision.decision_type.replace(/_/g, ' ')} />
        <Field label="Status" value={<StatusPill label={decision.status} variant={decision.status === 'pending_approval' ? 'warning' : 'neutral'} />} />
        <Field label="Approval required" value={decision.approval_required ? 'Yes' : 'No'} />
        <Field label="Actor" value={decision.actor_type || 'agent'} />
        <Field label="Confidence" value={decision.confidence || '—'} />
        <Field label="Created" value={<span title={fmtExact(decision.created_at)}>{fmtRelative(decision.created_at)}</span>} />
      </Section>
      <Section title="Evidence">
        <Field label="Health status" value={decision.health_status || '—'} />
        <Field label="Health score" value={decision.health_score == null ? '—' : `${decision.health_score}/100`} />
        <Field label="Triggered rule" value={decision.triggered_rule || '—'} />
        <Field label="Previous route" value={decision.previous_route || '—'} />
        <Field label="New route" value={decision.new_route || '—'} />
        <Field label="Correlation ID" value={decision.correlation_id || '—'} mono />
        <Field label="Decision ID" value={decision.id} mono />
      </Section>
    </Shell>
  );
}
