'use client';

/**
 * Screen 5 — Detection Details + Operational Integrity Analysis.
 *
 * Two side-by-side panels (stacked on narrow layouts) that open when a
 * detection row is selected. Between them they make the screen's argument
 * visible without a marketing sentence:
 *
 *     On-Chain Event        ✓
 *     Signer Validity       ✓
 *     Transfer-Agent Match  ✕
 *     Settlement Match      ✕
 *     -> CRITICAL OPERATIONAL ANOMALY
 *
 * Every value rendered here is a stored backend fact fetched from
 * /threat-monitoring/detections/{id}. Nothing is recomputed in the browser, and
 * a detection whose checks were never recorded says so rather than rendering an
 * empty (and therefore reassuring) check list.
 */
import { useEffect, useRef, useState } from 'react';

import { StatusPill } from '../components/ui-primitives';
import {
  CHECKS_UNAVAILABLE_COPY,
  checkGlyph,
  checkStatusColor,
  checkStatusLabel,
  conclusionColor,
  conclusionLabel,
  conclusionVariant,
  formatAmount,
  isOperationalIntegrity,
  isPreconfirmed,
  preconfirmationAge,
  reasonCodeLabel,
  telemetrySourceLabel,
  telemetryStageLabel,
  telemetryStageVariant,
  type OperationalAnalysis,
} from './operational-integrity';
import {
  CONFIDENCE_TOOLTIP,
  confidencePercent,
  detectionTypeLabel,
  relativeTime,
  severityLabel,
  severityVariant,
  shortHex,
  type DetectionRow,
} from './presentation';

export type DetectionDetail = DetectionRow & {
  operational_analysis?: OperationalAnalysis | null;
  score_inputs?: Record<string, unknown> | null;
};

/** One label/value row. A missing value reads "Not recorded", never a blank. */
function Field({ label, children, mono = false }: { label: string; children: React.ReactNode; mono?: boolean }) {
  return (
    <div className="tmField2">
      <span className="tmField2Label">{label}</span>
      <span className={mono ? 'tmField2Value tmField2Value--mono' : 'tmField2Value'}>
        {children}
      </span>
    </div>
  );
}

const NOT_RECORDED = <span className="muted">Not recorded</span>;

export default function DetectionDetailPanels({
  detectionId,
  authHeaders,
  onClose,
}: {
  detectionId: string;
  authHeaders: () => Record<string, string>;
  onClose: () => void;
}) {
  const [detail, setDetail] = useState<DetectionDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState('');
  const reqId = useRef(0);

  useEffect(() => {
    const id = ++reqId.current;
    setLoading(true);
    setErr('');
    setDetail(null);
    // GET only — selecting a detection never writes.
    fetch(`/api/threat-monitoring/detections/${encodeURIComponent(detectionId)}`, {
      headers: { ...authHeaders() },
      cache: 'no-store',
    })
      .then(async (res) => {
        if (id !== reqId.current) return;
        if (!res.ok) {
          setErr('Unable to load this detection right now.');
          return;
        }
        const payload = await res.json();
        setDetail((payload.detection ?? null) as DetectionDetail | null);
      })
      .catch(() => {
        if (id === reqId.current) setErr('Detection details are temporarily unavailable.');
      })
      .finally(() => {
        if (id === reqId.current) setLoading(false);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [detectionId, authHeaders]);

  const operational = detail ? isOperationalIntegrity(detail.category) : false;

  return (
    <section
      data-testid="detection-detail-panels"
      aria-label="Detection details"
      className="tmDetailPanels"
      // Two columns on desktop; the grid collapses to one on narrow layouts so
      // Details stacks above Analysis rather than overflowing horizontally.
      style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))',
        gap: '1.25rem',
        marginTop: '1.25rem',
      }}
    >
      <article className="dataCard" aria-label="Detection Details">
        <div className="tmDetailHead">
          <p className="sectionEyebrow tmDetailEyebrow">Detection Details</p>
          <button
            type="button"
            className="btn btn-secondary tmBtnSm"
            onClick={onClose}
            data-testid="close-detection-details"
          >
            Close
          </button>
        </div>
        {loading ? (
          <div className="skelBlock tmDetailSkeleton" aria-hidden="true" />
        ) : err ? (
          <p className="statusLine tmError" role="alert">{err}</p>
        ) : !detail ? (
          <p className="muted tmDetailNote">This detection is no longer available.</p>
        ) : (
          <div className="tmDetailFields" data-testid="detection-details-fields">
            <Field label="Detection">
              {detail.detection_type_label || detectionTypeLabel(detail.detection_type)}
            </Field>
            <Field label="Asset">{detail.asset_name ?? NOT_RECORDED}</Field>
            <Field label="Severity">
              <StatusPill label={severityLabel(detail.severity)} variant={severityVariant(detail.severity)} />
            </Field>
            {operational ? (
              <>
                <Field label="Operation">
                  {detail.operation ? String(detail.operation).toUpperCase() : NOT_RECORDED}
                </Field>
                <Field label="Observed Amount">
                  {detail.observed_amount === null || detail.observed_amount === undefined
                    ? NOT_RECORDED
                    : formatAmount(detail.observed_amount, detail.amount_decimals, {
                        signed: true,
                        unit: detail.amount_unit,
                      })}
                </Field>
                <Field label="Expected Amount">
                  {detail.expected_amount === null || detail.expected_amount === undefined
                    ? NOT_RECORDED
                    : formatAmount(detail.expected_amount, detail.amount_decimals, { unit: detail.amount_unit })}
                </Field>
                <Field label="Variance">
                  {detail.variance_amount === null || detail.variance_amount === undefined
                    ? NOT_RECORDED
                    : formatAmount(detail.variance_amount, detail.amount_decimals, {
                        signed: true,
                        unit: detail.amount_unit,
                      })}
                </Field>
              </>
            ) : null}
            <Field label="Source">
              {/* The ingestion lane that actually delivered this event, plus the
                  stage it can honestly claim. Never a "Flashblocks" or "200 ms"
                  badge that the runtime did not produce. */}
              {detail.telemetry_source ? (
                <span className="tmSourceValue">
                  {telemetrySourceLabel(detail.telemetry_source)}
                  <StatusPill
                    label={telemetryStageLabel(detail.telemetry_stage)}
                    variant={telemetryStageVariant(detail.telemetry_stage)}
                  />
                </span>
              ) : NOT_RECORDED}
            </Field>
            <Field label="Transaction Hash" mono>
              {detail.tx_hash ? (
                <span title={detail.tx_hash}>{shortHex(detail.tx_hash, 10, 8)}</span>
              ) : NOT_RECORDED}
            </Field>
            <Field label="Block">{detail.block_number ?? NOT_RECORDED}</Field>
            {isPreconfirmed(detail.telemetry_stage) && detail.preconfirmation_received_at ? (
              <Field label="Preconfirmation Age">
                {preconfirmationAge(detail.preconfirmation_received_at) ?? NOT_RECORDED}
              </Field>
            ) : null}
            <Field label="First Seen">{relativeTime(detail.first_seen_at)}</Field>
            <Field label="Evidence">{detail.evidence_count} artifacts</Field>
          </div>
        )}
      </article>

      <OperationalIntegrityAnalysis
        analysis={detail?.operational_analysis ?? null}
        applicable={operational}
        loading={loading}
        error={err}
      />
    </section>
  );
}

/**
 * The deterministic check panel. Its statuses come from the backend matcher;
 * this component chooses a glyph and a colour and nothing else.
 */
export function OperationalIntegrityAnalysis({
  analysis,
  applicable,
  loading,
  error,
}: {
  analysis: OperationalAnalysis | null;
  applicable: boolean;
  loading: boolean;
  error: string;
}) {
  return (
    <article className="dataCard" aria-label="Operational Integrity Analysis" data-testid="operational-integrity-analysis">
      <div className="tmDetailHead">
        <p className="sectionEyebrow tmDetailEyebrow">Operational Integrity Analysis</p>
      </div>
      {loading ? (
        <div className="skelBlock tmDetailSkeleton" aria-hidden="true" />
      ) : error ? (
        <p className="statusLine tmError" role="alert">{error}</p>
      ) : !applicable ? (
        <p className="muted tmDetailNote" data-testid="analysis-not-applicable">
          This is a cyber-security detection. Operational integrity checks reconcile an on-chain
          event against authorized business state and do not apply here.
        </p>
      ) : !analysis ? (
        <p className="muted tmDetailNote">{CHECKS_UNAVAILABLE_COPY}</p>
      ) : (
        <div className="tmAnalysisBody">
          {analysis.checks_available ? (
            <ul className="tmChecks" data-testid="operational-checks">
              {analysis.checks.map((check) => (
                <li
                  key={check.key}
                  className="tmCheck"
                  data-testid={`operational-check-${check.key}`}
                  data-status={String(check.status ?? '').toUpperCase()}
                >
                  <span
                    aria-hidden="true"
                    className="tmCheckGlyph"
                    // Colour from the canonical checkStatusColor(): PASS green, FAIL
                    // red, anything else (not evaluated) amber — never a pass.
                    style={{ color: checkStatusColor(check.status) }}
                  >
                    {checkGlyph(check.status)}
                  </span>
                  <span className="tmCheckText">
                    <span className="tmCheckLabel">
                      {check.label}
                      <span className="sr-only"> — {checkStatusLabel(check.status)}</span>
                    </span>
                    <span className="tmCheckReason">
                      {check.reason}
                    </span>
                    {check.source ? (
                      <span className="tmCheckSource">
                        Source: {check.source}
                      </span>
                    ) : null}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted tmDetailNote" data-testid="checks-unavailable">{CHECKS_UNAVAILABLE_COPY}</p>
          )}

          <div className="tmVerdict" data-tone={conclusionVariant(analysis.conclusion)}>
            <p className="metricLabel tmVerdictLabel">Conclusion</p>
            <p
              data-testid="operational-conclusion"
              data-conclusion={String(analysis.conclusion ?? '').toUpperCase()}
              className="tmVerdictValue"
              style={{ color: conclusionColor(analysis.conclusion) }}
            >
              {conclusionLabel(analysis.conclusion)}
            </p>
            <p className="muted tmVerdictReason">
              {reasonCodeLabel(analysis.deterministic_reason_code)}
              {analysis.matcher_version ? ` · ${analysis.matcher_version}` : ''}
            </p>
            <p className="metricMeta tmVerdictConfidence" title={CONFIDENCE_TOOLTIP}>
              Confidence {confidencePercent(analysis.confidence)}
            </p>
          </div>

          {analysis.narrative || analysis.ai_summary ? (
            <div className="tmNarrative">
              <p className="tmNarrativeText" data-testid="operational-narrative">
                {analysis.ai_summary || `${analysis.narrative?.finding ?? ''} ${analysis.narrative?.explanation ?? ''}`.trim()}
              </p>
              {/* Low-key authority label: the text above explains an already
                  decided verdict, it never produced one. */}
              <p className="muted tmNarrativeAuthority" data-testid="ai-authority-label">
                {analysis.ai_authority ?? 'AI Analysis: Explanation only'}
              </p>
            </div>
          ) : null}
        </div>
      )}
    </article>
  );
}
