'use client';

import { useEffect, useRef, type ReactNode } from 'react';

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

// P95 latency cell: truthful about sample sufficiency and recency. Only SUCCESSFUL
// samples count. 0 successful => "No successful samples"; 1–19 => "Insufficient
// samples"; 20+ => the calculated P95, labelled "historical" when its freshest sample
// is old or the provider is failing now — never a failed call's elapsed time.
export function p95LatencyCell(source: SourceRow) {
  const status = (source.p95_status ?? '').trim().toLowerCase();
  const count = source.p95_sample_count ?? 0;
  if (status === 'no_successful_samples' || (status === '' && source.p95_latency_ms == null && count === 0 && !source.p95_insufficient && source.median_latency_ms == null)) {
    return <span className="muted" title="No successful provider samples in the window">No successful samples</span>;
  }
  if (status === 'insufficient_samples' || source.p95_insufficient) {
    return (
      <span className="muted" title={`P95 needs 20+ successful samples (${count} so far)`}>
        Insufficient samples
      </span>
    );
  }
  if (source.p95_latency_ms == null) {
    return <span className="muted">—</span>;
  }
  if (source.p95_is_historical) {
    return (
      <span title={`Historical P95 from ${count} successful samples${source.p95_last_sample_at ? ` (last ${fmtRelative(source.p95_last_sample_at)})` : ''}; provider not verified live now`}>
        {fmtLatency(source.p95_latency_ms)} <span className="msrcTag">historical</span>
      </span>
    );
  }
  return <span>{fmtLatency(source.p95_latency_ms)}</span>;
}

function Field({ label, value, mono }: { label: string; value: ReactNode; mono?: boolean }) {
  return (
    <div className="msrcField">
      <span className="msrcFieldLabel">{label}</span>
      <span className={mono ? 'msrcFieldValue msrcMono' : 'msrcFieldValue'}>{value}</span>
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="msrcDrawerSection">
      <p className="sectionEyebrow msrcDrawerEyebrow">{title}</p>
      <div className="msrcDrawerSectionBody">{children}</div>
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
  // Keyboard: Escape closes, focus moves into the panel on open and returns to
  // the opener on close. Mount-only (the latest onClose is read through a ref)
  // so a background data refresh never steals focus from inside the drawer.
  const panelRef = useRef<HTMLDivElement>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    panelRef.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onCloseRef.current();
    };
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('keydown', onKey);
      opener?.focus?.();
    };
  }, []);

  return (
    <div role="dialog" aria-modal="true" aria-label={title} className="msrcDrawer">
      <button type="button" aria-label="Close drawer" onClick={onClose} className="msrcDrawerScrim" />
      <div className="msrcDrawerPanel" ref={panelRef} tabIndex={-1}>
        <div className="msrcDrawerHead">
          <div className="msrcDrawerHeadText">
            <h2 className="msrcDrawerTitle">{title}</h2>
            {subtitle ? <p className="msrcDrawerSub">{subtitle}</p> : null}
          </div>
          <button type="button" className="btn btn-secondary msrcRowBtn" onClick={onClose}>
            Close
          </button>
        </div>
        <div className="msrcDrawerBody">{children}</div>
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
        {/* Configured vs operational provider are DISTINCT truths: a configured provider
            in backoff shows Configured: <host> / Operational: None. */}
        <Field label="Configured provider" value={source.configured_primary_provider || source.primary_provider || '—'} />
        <Field label="Operational provider" value={source.operational_primary_provider || 'None'} />
        <Field label="Source type" value={source.source_type || '—'} />
        <Field
          label="Routing role"
          value={
            <StatusPill
              label={source.routing === 'unavailable' ? 'Unavailable' : source.routing ? source.routing : 'Unrouted'}
              variant={source.routing === 'primary' ? 'info' : source.routing === 'fallback' ? 'warning' : source.routing === 'unavailable' ? 'danger' : 'neutral'}
            />
          }
        />
        {source.provider_backoff_active ? (
          <Field label="Reason" value={<StatusPill label="Provider backoff active" variant="danger" />} />
        ) : null}
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
        {/* The value is the MEDIAN sample (median_latency_ms); P95 with its sample
            sufficiency rules lives in the table's P95 Latency column. */}
        <Field label="Median latency" value={source.median_latency_ms == null ? NOT_MEASURED : fmtLatency(source.median_latency_ms)} />
        <Field label="P95 latency" value={p95LatencyCell(source)} />
        <Field label="Error rate" value={source.error_rate == null ? NOT_MEASURED : `${(source.error_rate * 100).toFixed(2)}%`} />
        <Field label="Timeout rate" value={source.timeout_rate == null ? NOT_MEASURED : `${(source.timeout_rate * 100).toFixed(2)}%`} />
        <Field label="Block lag" value={source.block_lag == null ? NOT_MEASURED : String(source.block_lag)} />
        <Field label="Provider block" value={source.latest_block == null ? '—' : `#${source.latest_block.toLocaleString()}`} />
        <Field label="Last telemetry event" value={<span title={fmtExact(source.last_telemetry_at)}>{fmtRelative(source.last_telemetry_at)}</span>} />
        <Field label="Last heartbeat" value={<span title={fmtExact(source.last_heartbeat)}>{fmtRelative(source.last_heartbeat)}</span>} />
        <Field label="Last poll" value={<span title={fmtExact(source.last_poll_at)}>{fmtRelative(source.last_poll_at)}</span>} />
        {source.triggered_rules && source.triggered_rules.length > 0 ? (
          <p className="msrcDrawerNote">
            Triggered rules: {source.triggered_rules.join(', ')}
          </p>
        ) : null}
      </Section>

      <Section title="Routing History">
        {routingHistory.length === 0 ? (
          <p className="msrcDrawerEmpty">No routing decisions recorded for this source.</p>
        ) : (
          <ul className="msrcHistory">
            {routingHistory.slice(0, 6).map((decision) => (
              <li key={decision.id} className="msrcHistoryItem">
                <div className="msrcHistoryType">{decision.decision_type.replace(/_/g, ' ')}</div>
                <div className="msrcHistoryRoute">
                  {decision.previous_route || '—'} → {decision.new_route || '—'} · {decision.actor_type || 'agent'} · {fmtRelative(decision.created_at)}
                </div>
                {decision.triggered_rule ? <div className="msrcHistoryRule">Trigger: {decision.triggered_rule}</div> : null}
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title="Diagnostics">
        <p className="msrcDrawerNote">
          Live per-endpoint probes require the monitoring worker. Re-evaluate uses the deterministic engine over persisted evidence.
        </p>
        <button
          type="button"
          className="btn btn-primary msrcDrawerCta msrcIcoPulse"
          disabled={healthCheckBusy}
          onClick={onRunHealthCheck}
        >
          {healthCheckBusy ? 'Re-evaluating…' : 'Re-evaluate source health'}
        </button>
        <p className="msrcDiagCaption">Per-endpoint probes · not yet available</p>
        <div className="msrcDiagGrid">
          {diagnostics.map((label) => (
            <button
              key={label}
              type="button"
              className="btn btn-secondary msrcDiagBtn"
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
