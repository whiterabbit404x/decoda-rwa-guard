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
  evidenceQualityLabel,
  evidenceSourceLabel,
  evidenceSourceVariant,
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
  loading: boolean;
  error: string;
  investigating: boolean;
  investigateError: string;
  onInvestigate: (detectionId: string) => void;
  onViewEvidence: (detectionId: string) => void;
  onRetry: () => void;
};

export default function ThreatDetectionEngineerPanel({
  panel, degradedReasons, loading, error, investigating, investigateError, onInvestigate, onViewEvidence, onRetry,
}: Props) {
  return (
    <aside className="dataCard assessorPanel" aria-label="Threat Detection Engineer" data-testid="threat-detection-engineer-panel">
      <div className="assessorHeader">
        <p className="sectionEyebrow">Threat Detection Engineer</p>
        <h2 style={{ margin: '0.15rem 0 0', fontSize: '1.05rem' }}>Correlated exploit &amp; anomaly detection</h2>
      </div>

      {loading ? (
        <div className="assessorSkeleton" aria-hidden="true">
          <div className="skelBlock" style={{ height: '72px' }} />
          <div className="skelBlock" style={{ height: '48px' }} />
          <div className="skelBlock" style={{ height: '96px' }} />
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
  panel, degradedReasons, investigating, investigateError, onInvestigate, onViewEvidence,
}: {
  panel: EnginePanel;
  degradedReasons: string[];
  investigating: boolean;
  investigateError: string;
  onInvestigate: (id: string) => void;
  onViewEvidence: (id: string) => void;
}) {
  const detection = panel.detection;
  const presentation = enginePanelPresentation(panel.state, detection?.severity);
  const fields = panel.fields;
  // Start Investigation is only meaningful when a canonical detection exists. With
  // zero detections the primary action becomes Run Source Diagnostic.
  const showInvestigate = detection != null;

  return (
    <>
      {/* State headline — never a scary alert when evidence is thin. */}
      <section className="assessorSection">
        <div className="assessorStatusRow">
          <StatusPill label={presentation.eyebrow} variant={presentation.variant} />
        </div>
        <p className="assessorSummaryText" data-testid="engine-headline">{panel.headline}</p>
      </section>

      {/* Finding + explanation (truthful for the current canonical state). */}
      <section className="assessorSection">
        <ul className="assessorGapList">
          <li><span>Finding</span><strong data-testid="engine-finding">{panel.finding}</strong></li>
        </ul>
        <p className="assessorMeta" data-testid="engine-explanation" style={{ marginTop: '0.4rem' }}>{panel.explanation}</p>
      </section>

      {/* Canonical operational fields — derived from the backend summary only. */}
      <section className="assessorSection">
        <ul className="assessorGapList" data-testid="engine-fields">
          <li><span>Latest security telemetry</span><strong>{relativeTime(fields.last_security_telemetry_at)}</strong></li>
          <li><span>Worker heartbeat</span><strong>{relativeTime(fields.worker_heartbeat_at)}</strong></li>
          <li>
            <span>Ingestion status</span>
            <strong><StatusPill label={workerStatusLabel(fields.worker_status)} variant={workerStatusVariant(fields.worker_status)} /></strong>
          </li>
          <li><span>Evidence coverage</span><strong>{fields.evidence_coverage.live_backed}/{fields.evidence_coverage.active_detections} live-backed</strong></li>
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
          <div className="assessorStatusRow" style={{ flexWrap: 'wrap', gap: '0.4rem' }}>
            <StatusPill label={detectionTypeLabel(detection.detection_type)} variant="info" />
            <span title={SEVERITY_TOOLTIP}><StatusPill label={severityLabel(detection.severity)} variant={severityVariant(detection.severity)} /></span>
            <span title={CONFIDENCE_TOOLTIP}>
              <StatusPill label={`Confidence ${confidencePercent(detection.confidence)} (${confidenceBand(detection.confidence)})`} variant="neutral" />
            </span>
            <span title="Whether the supporting evidence is live customer telemetry, simulator, or replay.">
              <StatusPill label={evidenceSourceLabel(detection.evidence_source)} variant={evidenceSourceVariant(detection.evidence_source)} />
            </span>
          </div>
          <ul className="assessorGapList" style={{ marginTop: '0.6rem' }}>
            <li><span>Affected asset</span><strong>{detection.affected_asset_names[0] ?? '—'}</strong></li>
            <li><span>Evidence items</span><strong>{detection.evidence_count}</strong></li>
            <li><span title={EVIDENCE_QUALITY_TOOLTIP}>Data quality</span><strong>{evidenceQualityLabel(detection.evidence_quality)}</strong></li>
            <li><span>Last seen</span><strong>{relativeTime(detection.last_seen_at)}</strong></li>
          </ul>
          {detection.recommended_next_step ? (
            <p className="assessorMeta" style={{ marginTop: '0.5rem' }}>
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

      {/* Degraded banner (truthful). */}
      {degradedReasons.length > 0 ? (
        <section className="assessorSection">
          <div className="statusLine statusLine-warning" role="status" data-testid="engine-degraded">
            {degradedReasons.map((r) => degradedReasonCopy(r)).join(' ')}
          </div>
        </section>
      ) : null}

      {/* Recommendation copy for the no-detection state. */}
      {!detection ? (
        <p className="assessorMeta" data-testid="engine-recommendation" style={{ marginTop: '0.25rem' }}>
          <strong>Recommended action:</strong> Restore telemetry ingestion before beginning a new investigation.
        </p>
      ) : null}

      {/* Primary action. When there is a canonical detection: Start Investigation
          (disabled unless the backend says it is investigable). Otherwise the
          canonical action is Run Source Diagnostic — never a dead Start button. */}
      <div className="assessorActions">
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
