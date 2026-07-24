'use client';

import { useCallback, useEffect, useState } from 'react';

import { usePilotAuth } from './pilot-auth-context';
import { StatusPill } from './components/ui-primitives';
import {
  formatPercent,
  relativeTime,
  reserveStatusLabel,
  reserveStatusVariant,
} from './asset-risk-presentation';

type ReserveCoverage = {
  coverage_percent: number | null;
  status: string;
  assets_included: number;
  last_verified_at: string | null;
};

type RiskSummary = {
  total_assets: number;
  total_protected_value_usd: number;
  assessed_assets: number;
  risk_level_counts: { low: number; medium: number; high: number; critical: number };
  reserve_coverage: ReserveCoverage;
  anomaly_warnings: { assets: number; highest_severity: string | null };
  monitoring_gaps: { assets: number; missing_reserve_feed: number; stale_oracle: number; no_target: number; incomplete_provider: number };
  stale_feed_count: number;
  latest_assessment_at: string | null;
  data_completeness: number;
  confidence: number;
  ai_summary: string;
  ai_summary_source: string;
};

type Props = {
  refreshSignal?: number;
  onViewReport?: () => void;
  onFilterAnomalies?: () => void;
  onFilterGaps?: () => void;
};

// Ring gauge for the aggregate reserve coverage. Coverage is clamped to [0, 200]
// for the arc only (a 128% coverage over-fills past the 100% mark visibly).
function ReserveRing({ percent, variant }: { percent: number | null; variant: string }) {
  const pct = percent === null ? 0 : Math.max(0, Math.min(200, percent));
  const dash = Math.min(100, (pct / 200) * 100);
  const color =
    variant === 'danger' ? 'var(--danger-fg, #f87171)'
      : variant === 'warning' ? 'var(--warning-fg, #fbbf24)'
        : variant === 'success' ? 'var(--success-fg, #4ade80)'
          : 'var(--text-muted, #5a6478)';
  return (
    <div className="reserveRing" aria-hidden="true">
      <svg viewBox="0 0 42 42" width="96" height="96">
        <circle cx="21" cy="21" r="15.9" fill="none" stroke="var(--border, #2a3444)" strokeWidth="3" />
        <circle
          cx="21" cy="21" r="15.9" fill="none" stroke={color} strokeWidth="3" strokeLinecap="round"
          strokeDasharray={`${dash} ${100 - dash}`} strokeDashoffset="25" transform="rotate(-90 21 21)"
        />
      </svg>
      <div className="reserveRingLabel">
        <strong>{percent === null ? '--' : `${Math.round(percent)}%`}</strong>
      </div>
    </div>
  );
}

export default function AssetRiskAssessorPanel({ refreshSignal, onViewReport, onFilterAnomalies, onFilterGaps }: Props) {
  const { authHeaders, signOut } = usePilotAuth();
  const [summary, setSummary] = useState<RiskSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setError('');
    const headers = authHeaders();
    if (!headers.Authorization) {
      setError('Your session is missing or expired. Please sign in again.');
      setLoading(false);
      return;
    }
    try {
      const response = await fetch('/api/assets/risk-summary', { headers: { ...headers }, cache: 'no-store' });
      if (response.status === 401 || response.status === 403) {
        await signOut();
        setError('Your session is missing or expired. Please sign in again.');
        return;
      }
      if (!response.ok) {
        setError('Unable to load the risk summary right now.');
        return;
      }
      const payload = await response.json();
      setSummary(payload.summary ?? null);
    } catch {
      setError('The assessor summary is temporarily unavailable.');
    } finally {
      setLoading(false);
    }
  }, [authHeaders, signOut]);

  useEffect(() => { void load(); }, [load, refreshSignal]);

  return (
    <aside className="dataCard assessorPanel" aria-label="AI Asset Risk Assessor">
      <div className="assessorHeader">
        <p className="sectionEyebrow">AI Asset Risk Assessor</p>
        <h2 style={{ margin: '0.15rem 0 0', fontSize: '1.05rem' }}>Continuous reserve and exposure analysis</h2>
      </div>

      {loading ? (
        <div className="assessorSkeleton" aria-hidden="true">
          <div className="skelBlock" style={{ height: '96px' }} />
          <div className="skelBlock" style={{ height: '48px' }} />
          <div className="skelBlock" style={{ height: '64px' }} />
        </div>
      ) : error ? (
        <div className="assessorSection">
          <p className="statusLine" role="alert">{error}</p>
          <button type="button" className="btn btn-secondary" onClick={() => { setLoading(true); void load(); }}>Retry</button>
        </div>
      ) : !summary ? (
        <div className="assessorSection">
          <p className="muted">No summary available yet.</p>
        </div>
      ) : (
        <>
          {/* Reserve Coverage */}
          <section className="assessorSection">
            <h3 className="assessorSectionTitle">Reserve Coverage</h3>
            <div className="reserveCoverageRow">
              <ReserveRing percent={summary.reserve_coverage.coverage_percent} variant={reserveStatusVariant(summary.reserve_coverage.status)} />
              <div>
                <StatusPill label={reserveStatusLabel(summary.reserve_coverage.status)} variant={reserveStatusVariant(summary.reserve_coverage.status)} />
                <p className="assessorMeta">
                  {summary.reserve_coverage.assets_included} asset{summary.reserve_coverage.assets_included === 1 ? '' : 's'} with verified reserves
                </p>
                <p className="assessorMeta">
                  {summary.reserve_coverage.status === 'insufficient_evidence'
                    ? 'Coverage cannot be verified for the current set.'
                    : `Last verified ${relativeTime(summary.reserve_coverage.last_verified_at)}`}
                </p>
              </div>
            </div>
          </section>

          {/* Anomaly Warnings */}
          <section className="assessorSection">
            <h3 className="assessorSectionTitle">Anomaly Warnings</h3>
            <div className="assessorStatRow">
              <span className="assessorStatValue">{summary.anomaly_warnings.assets}</span>
              <div>
                <p className="assessorMeta">asset{summary.anomaly_warnings.assets === 1 ? '' : 's'} with active anomalies</p>
                {summary.anomaly_warnings.highest_severity ? (
                  <StatusPill
                    label={`Highest: ${summary.anomaly_warnings.highest_severity}`}
                    variant={summary.anomaly_warnings.highest_severity === 'critical' || summary.anomaly_warnings.highest_severity === 'high' ? 'danger' : 'warning'}
                  />
                ) : <span className="assessorMeta">No active anomalies</span>}
              </div>
            </div>
            {summary.anomaly_warnings.assets > 0 && onFilterAnomalies ? (
              <button type="button" className="assessorLink" onClick={onFilterAnomalies}>View flagged assets →</button>
            ) : null}
          </section>

          {/* Monitoring Gaps */}
          <section className="assessorSection">
            <h3 className="assessorSectionTitle">Monitoring Gaps</h3>
            <ul className="assessorGapList">
              <li><span>Missing reserve feed</span><strong>{summary.monitoring_gaps.missing_reserve_feed}</strong></li>
              <li><span>Stale oracle / reserve data</span><strong>{summary.monitoring_gaps.stale_oracle}</strong></li>
              <li><span>No linked monitoring target</span><strong>{summary.monitoring_gaps.no_target}</strong></li>
              <li><span>Incomplete provider coverage</span><strong>{summary.monitoring_gaps.incomplete_provider}</strong></li>
            </ul>
            {summary.monitoring_gaps.assets > 0 && onFilterGaps ? (
              <button type="button" className="assessorLink" onClick={onFilterGaps}>View assets with gaps →</button>
            ) : null}
          </section>

          {/* AI summary */}
          <section className="assessorSection">
            <h3 className="assessorSectionTitle">Summary</h3>
            <p className="assessorSummaryText">{summary.ai_summary}</p>
            <p className="assessorProvenance">
              {summary.ai_summary_source === 'ai' ? 'AI-generated from structured assessment results.' : 'Generated from canonical assessment results.'}
              {' '}Confidence {Math.round((summary.confidence || 0) * 100)}% · {summary.assessed_assets}/{summary.total_assets} assessed
              {summary.latest_assessment_at ? ` · updated ${relativeTime(summary.latest_assessment_at)}` : ''}
            </p>
          </section>

          <div className="assessorActions">
            <button type="button" className="btn btn-primary" onClick={onViewReport}>View full risk report</button>
            <button type="button" className="btn btn-secondary" onClick={() => { setLoading(true); void load(); }}>Refresh</button>
          </div>
        </>
      )}
    </aside>
  );
}

export type { RiskSummary };
