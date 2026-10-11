'use client';

import { StatusPill } from '../components/ui-primitives';
import {
  CONFIDENCE_TOOLTIP,
  EVIDENCE_QUALITY_TOOLTIP,
  RUN_SOURCE_DIAGNOSTIC_LABEL,
  SEVERITY_TOOLTIP,
  SOURCE_DIAGNOSTIC_HREF,
  confidencePercent,
  confidenceBand,
  detectionTypeLabel,
  degradedReasonCopy,
  enginePanelPresentation,
  evidenceCoverageLabel,
  evidenceQualityLabel,
  evidenceSourceLabel,
  evidenceSourceVariant,
  formatDate,
  ingestionBadge,
  relativeTime,
  severityLabel,
  severityVariant,
  workerStatusLabel,
  workerStatusVariant,
  type EnginePanel,
} from './presentation';

type Props = {
  panel: EnginePanel | null;
  degradedReasons: string[];
  windowText: string;
  loading: boolean;
  error: string;
  investigating: boolean;
  investigateError: string;
  onInvestigate: (detectionId: string) => void;
  onViewEvidence: (detectionId: string) => void;
  onRetry: () => void;
};

export default function ThreatDetectionEngineerPanel({
  panel, degradedReasons, windowText, loading, error, investigating, investigateError, onInvestigate, onViewEvidence, onRetry,
}: Props) {
  return (
    <aside className="dataCard assessorPanel tmEngine" aria-label="Threat Detection Engineer" data-testid="threat-detection-engineer-panel">
      <div className="assessorHeader tmEngineHeader">
        <span className="tmEngineMark" aria-hidden="true">
          <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" focusable="false">
            <path d="M12 3 4.5 6v5.5c0 4.6 3.2 8.4 7.5 9.5 4.3-1.1 7.5-4.9 7.5-9.5V6L12 3Z" />
            <path d="M12 8.5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7Zm0 3.5h.01" />
          </svg>
        </span>
        <div className="tmEngineTitles">
          <h2 className="sectionEyebrow tmEngineTitle">Threat Detection Engineer</h2>
          {/* Purpose descriptor — kept muted and specific to what the panel does, so the
              canonical Finding below is the prominent state statement (never a generic
              feature tagline). */}
          <p className="assessorMeta tmEnginePurpose" data-testid="engine-purpose">
            Autonomous review of the current threat state
          </p>
        </div>
      </div>

      {loading ? (
        <div className="assessorSkeleton" aria-hidden="true">
          <div className="skelBlock tmSkel72" />
          <div className="skelBlock tmSkel48" />
          <div className="skelBlock tmSkel96" />
        </div>
      ) : error ? (
        <div className="assessorSection">
          <p className="statusLine" role="alert">{error}</p>
          <button type="button" className="btn btn-secondary" onClick={onRetry}>Retry</button>
        </div>
      ) : !panel ? (
        <div className="assessorSection">
          <p className="muted">No summary available yet.</p>
        </div>
      ) : (
        <PanelBody
          panel={panel}
          degradedReasons={degradedReasons}
          windowText={windowText}
          investigating={investigating}
          investigateError={investigateError}
          onInvestigate={onInvestigate}
          onViewEvidence={onViewEvidence}
        />
      )}
    </aside>
  );
}

function PanelBody({
  panel, degradedReasons, windowText, investigating, investigateError, onInvestigate, onViewEvidence,
}: {
  panel: EnginePanel;
  degradedReasons: string[];
  windowText: string;
  investigating: boolean;
  investigateError: string;
  onInvestigate: (id: string) => void;
  onViewEvidence: (id: string) => void;
}) {
  const detection = panel.detection;
  const presentation = enginePanelPresentation(panel.state, detection?.severity);
  const fields = panel.fields;
  // Status badge: for the no-detection states derive an ingestion-truthful badge
  // ("Ingestion stale") from canonical worker status; keep the state eyebrow when a
  // material detection exists (e.g. "Threat detected").
  const badge = detection != null
    ? { label: presentation.eyebrow, variant: presentation.variant }
    : ingestionBadge(fields.worker_status, fields.ingestion_status);
  // Degraded reasons already conveyed by the headline/finding are dropped so the
  // panel never states the same "no telemetry"/"stale" sentence twice.
  const REDUNDANT_REASONS = new Set(['no_telemetry', 'no_security_telemetry_in_window', 'telemetry_stale', 'worker_unhealthy']);
  const extraDegraded = degradedReasons.filter((r) => !REDUNDANT_REASONS.has(r));
  // Start Investigation is only meaningful when a canonical detection exists. With
  // zero detections the primary action becomes Run Source Diagnostic.
  const showInvestigate = detection != null;

  return (
    <>
      {/* State headline — never a scary alert when evidence is thin. The section's
          tone is the badge variant itself (canonical helpers), so the accent edge can
          never read calmer than the badge it sits beside. */}
      <section className="assessorSection tmEngineState" data-tone={badge.variant}>
        <div className="assessorStatusRow">
          <StatusPill label={badge.label} variant={badge.variant} />
        </div>
        <p className="assessorSummaryText tmEngineHeadline" data-testid="engine-headline">{panel.headline}</p>
      </section>

      {/* Finding + explanation (truthful for the current canonical state). */}
      <section className="assessorSection">
        <ul className="assessorGapList tmEngineFinding">
          <li><span>Finding</span><strong data-testid="engine-finding">{panel.finding}</strong></li>
        </ul>
        <p className="assessorMeta tmEngineExplanation" data-testid="engine-explanation">{panel.explanation}</p>
      </section>

      {/* Canonical operational fields — derived from the backend summary only. */}
      <section className="assessorSection">
        <ul className="assessorGapList tmEngineFields" data-testid="engine-fields">
          <li><span>Selected window</span><strong>{windowText}</strong></li>
          {/* Latest security telemetry is a historical fact → absolute date; heartbeat
              is a liveness age → relative "N ago". Both derive from the canonical
              summary, so neither collapses to "—" while a timestamp exists. */}
          <li><span>Latest security telemetry</span><strong data-testid="engine-latest-telemetry">{formatDate(fields.last_security_telemetry_at)}</strong></li>
          <li><span>Worker heartbeat</span><strong data-testid="engine-worker-heartbeat">{relativeTime(fields.worker_heartbeat_at)}</strong></li>
          <li>
            <span>Ingestion status</span>
            <strong><StatusPill label={workerStatusLabel(fields.worker_status)} variant={workerStatusVariant(fields.worker_status)} /></strong>
          </li>
          <li><span>Evidence coverage</span><strong data-testid="engine-evidence-coverage">{evidenceCoverageLabel(fields.evidence_coverage)}</strong></li>
          <li>
            <span title={CONFIDENCE_TOOLTIP}>Detection confidence</span>
            <strong>{fields.detection_confidence != null ? `${confidencePercent(fields.detection_confidence)} (${confidenceBand(fields.detection_confidence)})` : 'Unavailable'}</strong>
          </li>
        </ul>
      </section>

      {/* Detected pattern detail (only when a material detection exists). */}
      {detection ? (
        <section className="assessorSection">
          <h3 className="assessorSectionTitle">Detected pattern</h3>
          <div className="assessorStatusRow tmEngineChips">
            <StatusPill label={detectionTypeLabel(detection.detection_type)} variant="info" />
            <span title={SEVERITY_TOOLTIP}><StatusPill label={severityLabel(detection.severity)} variant={severityVariant(detection.severity)} /></span>
            <span title={CONFIDENCE_TOOLTIP}>
              <StatusPill label={`Confidence ${confidencePercent(detection.confidence)} (${confidenceBand(detection.confidence)})`} variant="neutral" />
            </span>
            <span title="Whether the supporting evidence is live customer telemetry, simulator, or replay.">
              <StatusPill label={evidenceSourceLabel(detection.evidence_source)} variant={evidenceSourceVariant(detection.evidence_source)} />
            </span>
          </div>
          <ul className="assessorGapList tmEngineFields tmEnginePattern">
            <li><span>Affected asset</span><strong>{detection.affected_asset_names[0] ?? '—'}</strong></li>
            <li><span>Evidence items</span><strong>{detection.evidence_count}</strong></li>
            <li><span title={EVIDENCE_QUALITY_TOOLTIP}>Data quality</span><strong>{evidenceQualityLabel(detection.evidence_quality)}</strong></li>
            <li><span>Last seen</span><strong>{relativeTime(detection.last_seen_at)}</strong></li>
          </ul>
          {detection.recommended_next_step ? (
            <p className="assessorMeta tmEngineNext">
              <strong>Recommended next step:</strong> {detection.recommended_next_step}
            </p>
          ) : null}
        </section>
      ) : null}

      {/* AI / deterministic narrative provenance. */}
      {detection?.ai_summary ? (
        <section className="assessorSection">
          <h3 className="assessorSectionTitle">Summary</h3>
          <p className="assessorSummaryText">{detection.ai_summary}</p>
          <p className="assessorProvenance">
            {panel.ai_summary_source === 'ai'
              ? 'AI-generated from stored detection evidence.'
              : 'Generated from stored detection evidence.'}
          </p>
        </section>
      ) : null}

      {/* Degraded banner (truthful) — only for reasons NOT already stated by the
          headline/finding above, so the same "no telemetry"/"stale" sentence is
          never repeated across sections. */}
      {extraDegraded.length > 0 ? (
        <section className="assessorSection">
          <div className="statusLine statusLine-warning tmNotice" role="status" data-testid="engine-degraded" data-tone="warning">
            {extraDegraded.map((r) => degradedReasonCopy(r)).join(' ')}
          </div>
        </section>
      ) : null}

      {/* Recommendation copy for the no-detection state. */}
      {!detection ? (
        <p className="assessorMeta tmEngineNext" data-testid="engine-recommendation">
          <strong>Recommended action:</strong> Restore telemetry ingestion before beginning a new investigation.
        </p>
      ) : null}

      {/* Primary action. When there is a canonical detection: Start Investigation
          (disabled unless the backend says it is investigable). Otherwise the
          canonical action is Run Source Diagnostic — never a dead Start button. */}
      <div className="assessorActions tmEngineActions">
        {showInvestigate ? (
          <button
            type="button"
            className="btn btn-primary"
            data-testid="start-investigation"
            disabled={!panel.can_investigate || !detection || investigating}
            aria-busy={investigating}
            title={
              !detection
                ? 'No material detection to investigate.'
                : !panel.can_investigate
                  ? 'This detection is below investigation criteria.'
                  : 'Open an investigation from this detection.'
            }
            onClick={() => { if (detection && panel.can_investigate && !investigating) onInvestigate(detection.detection_id); }}
          >
            {investigating ? 'Opening investigation…' : 'Start Investigation'}
          </button>
        ) : (
          <a
            href={SOURCE_DIAGNOSTIC_HREF}
            className="btn btn-primary"
            data-testid="run-source-diagnostic"
          >
            {RUN_SOURCE_DIAGNOSTIC_LABEL}
          </a>
        )}
        {detection ? (
          <button type="button" className="btn btn-secondary" data-testid="view-evidence" onClick={() => onViewEvidence(detection.detection_id)}>
            View Evidence
          </button>
        ) : null}
      </div>
      {investigateError ? (
        <p className="assessorMeta assessorWorkerError" role="alert" data-testid="investigate-error">{investigateError}</p>
      ) : null}
    </>
  );
}
