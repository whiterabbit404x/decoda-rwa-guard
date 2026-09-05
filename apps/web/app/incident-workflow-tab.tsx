'use client';

import { useEffect, useState } from 'react';

import { StatusPill } from './components/ui-primitives';
import { usePilotAuth } from './pilot-auth-context';
import {
  workflowStateLabel,
  workflowStateVariant,
  type WorkflowStage,
} from './forensic-investigation-presentation';
import {
  coverageStateLabel,
  coverageStateVariant,
  investigationCoverage,
  loadStateFor,
  summarizeWorkflowProgress,
  type CaseResponseAction,
  type ForensicLoadState,
  type IncidentCaseSummary,
} from './incident-forensics-presentation';

// Same-origin proxy base — the Incidents UI never calls the backend directly.
const API_PROXY_BASE = '/api';

/**
 * Screen 7 — Workflow tab.
 *
 * The canonical investigation stage model, read from `GET /incidents/{id}/workflow`.
 * That endpoint returns the backend's own `WORKFLOW_STAGES` tuple with each stage's
 * state derived from persisted facts — the alert row, the evidence snapshot's
 * telemetry, the AI triage job's status, the recommendation rows, the generated
 * report. The browser counts those stages; it never defines them, and it never
 * infers "done" from an unrelated boolean.
 *
 * That is what makes the Case File's "N / M complete" answerable: the denominator
 * is the length of this list, and every stage on it names the persisted fact behind
 * its state.
 *
 * Beneath the stages, Investigation Coverage answers a different question — which
 * lifecycle domains have a persisted record at all. A domain with none is reported
 * as Missing (or Not applicable, where the incident's origin means it was never
 * expected). Nothing is back-filled to make the flow look complete.
 */
export default function IncidentWorkflowTab({ incidentId, summary, summaryLoad, responseActions, responseLoad }: {
  incidentId: string;
  summary: IncidentCaseSummary | null;
  summaryLoad: ForensicLoadState;
  responseActions: readonly CaseResponseAction[];
  responseLoad: ForensicLoadState;
}) {
  const { authHeaders } = usePilotAuth();
  const [stages, setStages] = useState<WorkflowStage[]>([]);
  const [load, setLoad] = useState<ForensicLoadState>('idle');
  // The forensic layer can be off for a deployment. That is not an error and not
  // an empty workflow — it is "this deployment does not have it", said plainly.
  const [unavailable, setUnavailable] = useState(false);

  useEffect(() => {
    if (!incidentId) { setStages([]); setLoad('idle'); return; }
    let cancelled = false;
    const controller = new AbortController();
    setLoad('loading');
    void fetch(`${API_PROXY_BASE}/incidents/${encodeURIComponent(incidentId)}/workflow`, {
      headers: authHeaders(),
      cache: 'no-store',
      signal: controller.signal,
    })
      .then(async (res) => {
        if (cancelled) return;
        if (!res.ok) {
          setStages([]);
          setUnavailable(false);
          setLoad(loadStateFor(res.status, false));
          return;
        }
        const json = (await res.json()) as { status?: string; stages?: WorkflowStage[] };
        if (cancelled) return;
        const rows = json?.stages ?? [];
        setUnavailable(json?.status === 'unavailable');
        setStages(rows);
        setLoad(loadStateFor(res.status, rows.length > 0));
      })
      .catch(() => {
        if (cancelled) return;
        setStages([]);
        setUnavailable(false);
        setLoad('error');
      });
    return () => { cancelled = true; controller.abort(); };
  }, [authHeaders, incidentId]);

  const progress = summarizeWorkflowProgress(stages);
  // The stage an operator is on now: the first that is running or queued. Marked
  // rather than recoloured, so "current" never reads as "complete".
  const currentStage = stages.find((s) => s.state === 'in_progress')
    ?? stages.find((s) => s.state === 'queued')
    ?? null;
  const coverage = investigationCoverage({
    summary,
    summaryLoad,
    responseTotal: responseActions.length,
    responseLoad,
  });

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
      <section aria-label="Investigation stages">
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '0.5rem', marginBottom: '0.5rem' }}>
          <p className="sectionEyebrow" style={{ margin: 0 }}>Investigation stages</p>
          {progress ? (
            <span className="tableMeta" style={{ fontSize: '0.75rem' }}>
              {progress.completed} of {progress.total} stages complete
            </span>
          ) : null}
        </div>
        <WorkflowStages
          stages={stages}
          load={load}
          unavailable={unavailable}
          currentStage={currentStage?.stage ?? null}
        />
        {load === 'ready' ? (
          <p className="tableMeta" style={{ margin: '0.5rem 0 0', fontSize: '0.7rem' }}>
            Stage states are derived from persisted records by the backend — the alert, the evidence
            snapshot, the triage job, the recommendation rows and the generated report. This view counts
            them; it does not define them.
          </p>
        ) : null}
      </section>

      <section aria-label="Investigation coverage">
        <p className="sectionEyebrow" style={{ margin: '0 0 0.5rem' }}>Investigation coverage</p>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.3rem' }}>
          {coverage.map((row) => (
            <div key={row.key} className="incidentCaseLine">
              <span className="incidentCaseLineLabel">{row.label}</span>
              <span className="incidentCaseLineValue">
                <StatusPill label={coverageStateLabel(row.state)} variant={coverageStateVariant(row.state)} />
              </span>
            </div>
          ))}
        </div>
        <p className="tableMeta" style={{ margin: '0.5rem 0 0', fontSize: '0.7rem' }}>
          A domain with no persisted record is reported as missing rather than filled in. No timeline
          event or timestamp is manufactured to complete the flow.
        </p>
      </section>
    </div>
  );
}

/* ── The canonical stages ─────────────────────────────────────────── */
function WorkflowStages({ stages, load, unavailable, currentStage }: {
  stages: readonly WorkflowStage[];
  load: ForensicLoadState;
  unavailable: boolean;
  currentStage: string | null;
}) {
  if (load === 'idle' || load === 'loading') {
    return <p className="muted" style={{ fontSize: '0.85rem' }} aria-busy="true">Loading investigation workflow…</p>;
  }
  if (load === 'unauthorized') {
    return (
      <p className="muted" style={{ fontSize: '0.85rem' }} role="alert">
        You do not have permission to view this incident&apos;s workflow in the current workspace.
      </p>
    );
  }
  if (load === 'not_found') {
    return <p className="muted" style={{ fontSize: '0.85rem' }} role="alert">This incident could not be found in the current workspace.</p>;
  }
  if (load === 'error') {
    return (
      <p className="muted" style={{ fontSize: '0.85rem' }} role="alert">
        Workflow unavailable — the investigation record could not be read. No partial progress is shown as complete.
      </p>
    );
  }
  if (unavailable) {
    return (
      <p className="muted" style={{ fontSize: '0.85rem' }}>
        The forensic investigation layer is not enabled for this deployment, so no workflow stages are recorded.
      </p>
    );
  }
  if (stages.length === 0) {
    return <p className="muted" style={{ fontSize: '0.85rem' }}>No workflow stages have been recorded for this incident.</p>;
  }
  return (
    <ol style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: '0.3rem' }}>
      {stages.map((stage) => (
        <li key={stage.stage} className="incidentCaseLine">
          <span className="incidentCaseLineLabel">{stage.label}</span>
          <span className="incidentCaseLineValue" style={{ display: 'inline-flex', alignItems: 'center', gap: '0.35rem', flexWrap: 'wrap' }}>
            <StatusPill label={workflowStateLabel(stage.state)} variant={workflowStateVariant(stage.state)} />
            {/* "Current" marks where the operator is, beside the state rather than
                replacing it — an in-progress stage is not a completed one. */}
            {stage.stage === currentStage ? <StatusPill label="Current" variant="info" /> : null}
          </span>
        </li>
      ))}
    </ol>
  );
}
