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
    <div style={{ display: 'flex', justifyContent: 'space-between', gap: '1rem', padding: '0.35rem 0', borderBottom: '1px solid var(--border-subtle, rgba(148,163,184,0.12))' }}>
      <span style={{ color: 'var(--text-muted)', fontSize: '0.82rem', whiteSpace: 'nowrap' }}>{label}</span>
      <span
        style={{
          color: 'var(--text-primary)',
          fontSize: '0.85rem',
          textAlign: 'right',
          fontFamily: mono ? 'monospace' : undefined,
          wordBreak: mono ? 'break-all' : 'normal',
        }}
      >
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
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '0.5rem' }}>
          <p className="sectionEyebrow" style={{ margin: 0 }}>Detection Details</p>
          <button
            type="button"
            className="btn btn-secondary"
            style={{ fontSize: '0.75rem', padding: '0.2rem 0.5rem' }}
            onClick={onClose}
            data-testid="close-detection-details"
          >
            Close
          </button>
        </div>
        {loading ? (
          <div className="skelBlock" style={{ height: '10rem', marginTop: '0.75rem' }} aria-hidden="true" />
        ) : err ? (
          <p className="statusLine" role="alert" style={{ color: 'var(--danger-fg)' }}>{err}</p>
        ) : !detail ? (
          <p className="muted" style={{ marginTop: '0.75rem' }}>This detection is no longer available.</p>
        ) : (
          <div style={{ marginTop: '0.75rem' }} data-testid="detection-details-fields">
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
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: '0.35rem', flexWrap: 'wrap', justifyContent: 'flex-end' }}>
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
      <p className="sectionEyebrow" style={{ margin: 0 }}>Operational Integrity Analysis</p>
      {loading ? (
        <div className="skelBlock" style={{ height: '10rem', marginTop: '0.75rem' }} aria-hidden="true" />
      ) : error ? (
        <p className="statusLine" role="alert" style={{ color: 'var(--danger-fg)' }}>{error}</p>
      ) : !applicable ? (
        <p className="muted" style={{ marginTop: '0.75rem', fontSize: '0.85rem' }} data-testid="analysis-not-applicable">
          This is a cyber-security detection. Operational integrity checks reconcile an on-chain
          event against authorized business state and do not apply here.
        </p>
      ) : !analysis ? (
        <p className="muted" style={{ marginTop: '0.75rem', fontSize: '0.85rem' }}>{CHECKS_UNAVAILABLE_COPY}</p>
      ) : (
        <div style={{ marginTop: '0.75rem' }}>
          {analysis.checks_available ? (
            <ul
              style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: '0.6rem' }}
              data-testid="operational-checks"
            >
              {analysis.checks.map((check) => (
                <li
                  key={check.key}
                  data-testid={`operational-check-${check.key}`}
                  data-status={String(check.status ?? '').toUpperCase()}
                  style={{ display: 'flex', gap: '0.6rem', alignItems: 'flex-start' }}
                >
                  <span
                    aria-hidden="true"
                    style={{ color: checkStatusColor(check.status), fontWeight: 700, lineHeight: 1.4, width: '1rem' }}
                  >
                    {checkGlyph(check.status)}
                  </span>
                  <span style={{ minWidth: 0 }}>
                    <span style={{ display: 'block', fontWeight: 600, fontSize: '0.85rem', color: 'var(--text-primary)' }}>
                      {check.label}
                      <span className="sr-only"> — {checkStatusLabel(check.status)}</span>
                    </span>
                    <span style={{ display: 'block', fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
                      {check.reason}
                    </span>
                    {check.source ? (
                      <span style={{ display: 'block', fontSize: '0.72rem', color: 'var(--text-muted)' }}>
                        Source: {check.source}
                      </span>
                    ) : null}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted" style={{ fontSize: '0.85rem' }} data-testid="checks-unavailable">{CHECKS_UNAVAILABLE_COPY}</p>
          )}

          <div style={{ marginTop: '1rem', paddingTop: '0.75rem', borderTop: '1px solid var(--border-subtle, rgba(148,163,184,0.18))' }}>
            <p className="metricLabel" style={{ margin: 0 }}>Conclusion</p>
            <p
              data-testid="operational-conclusion"
              data-conclusion={String(analysis.conclusion ?? '').toUpperCase()}
              style={{ margin: '0.25rem 0 0', fontWeight: 700, letterSpacing: '0.02em', color: conclusionColor(analysis.conclusion) }}
            >
              {conclusionLabel(analysis.conclusion)}
            </p>
            <p className="muted" style={{ margin: '0.35rem 0 0', fontSize: '0.8rem' }}>
              {reasonCodeLabel(analysis.deterministic_reason_code)}
              {analysis.matcher_version ? ` · ${analysis.matcher_version}` : ''}
            </p>
            <p className="metricMeta" style={{ margin: '0.5rem 0 0' }} title={CONFIDENCE_TOOLTIP}>
              Confidence {confidencePercent(analysis.confidence)}
            </p>
          </div>

          {analysis.narrative || analysis.ai_summary ? (
            <div style={{ marginTop: '0.9rem', paddingTop: '0.75rem', borderTop: '1px solid var(--border-subtle, rgba(148,163,184,0.18))' }}>
              <p style={{ margin: 0, fontSize: '0.82rem', color: 'var(--text-secondary)', lineHeight: 1.5 }} data-testid="operational-narrative">
                {analysis.ai_summary || `${analysis.narrative?.finding ?? ''} ${analysis.narrative?.explanation ?? ''}`.trim()}
              </p>
              {/* Low-key authority label: the text above explains an already
                  decided verdict, it never produced one. */}
              <p className="muted" style={{ margin: '0.4rem 0 0', fontSize: '0.72rem' }} data-testid="ai-authority-label">
                {analysis.ai_authority ?? 'AI Analysis: Explanation only'}
              </p>
            </div>
          ) : null}
        </div>
      )}
    </article>
  );
}
