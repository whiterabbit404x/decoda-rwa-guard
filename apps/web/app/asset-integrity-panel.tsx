'use client';

/**
 * Screen 3 — Asset Integrity / Reconciliation panel.
 *
 * Renders the deterministic reconciliation verdict for one asset:
 *
 *   ON-CHAIN STATE (left)        AUTHORITATIVE STATE (right)
 *   RECONCILIATION RESULT        AI ASSET RISK ASSESSOR
 *
 * Everything shown here is a backend fact. This component performs NO
 * reconciliation math: it does not compute a supply, a variance, an
 * authorization outcome, a reason code, or a severity. It formats what
 * /assets/{id}/integrity returned and nothing else.
 *
 * The GET is side-effect free, so opening or refreshing this tab never creates
 * a reconciliation snapshot, a detection, or an incident.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';

import { usePilotAuth } from './pilot-auth-context';
import { StatusPill } from './components/ui-primitives';
import {
  formatEvidenceCount,
  formatRule,
  formatSupply,
  formatVarianceUnits,
  freshnessLabel,
  integrityPanelState,
  investigateCta,
  isAnomalyStatus,
  isIndeterminateStatus,
  reasonCodeLabel,
  reconciliationStatusLabel,
  reconciliationStatusMeaning,
  reconciliationStatusVariant,
  relativeTime,
  severityVariant,
  truncateHex,
  varianceDirection,
} from './asset-integrity-presentation';

type Props = {
  assetId: string;
  /** 'integrity' renders the reconciliation panels; 'onchain' / 'offchain' render
   *  the single corresponding state card; 'history' renders the snapshot table. */
  view?: 'integrity' | 'onchain' | 'offchain' | 'history';
};

/** A value that the backend could not establish. Never rendered as 0 or as OK. */
function Unavailable({ label = 'Unavailable' }: { label?: string }) {
  return <span className="integrityUnavailable">{label}</span>;
}

function Row({ label, children, mono = false }: { label: string; children: React.ReactNode; mono?: boolean }) {
  return (
    <div className="integrityKvRow">
      <span className="integrityKvLabel">{label}</span>
      <span className={mono ? 'integrityKvValue integrityMono' : 'integrityKvValue'}>{children}</span>
    </div>
  );
}

/* ── ON-CHAIN STATE ────────────────────────────────────────────────── */
function OnChainStateCard({ state }: { state: any }) {
  const available = Boolean(state?.available);
  return (
    <section className="integrityCard" aria-label="On-chain state">
      <header className="integrityCardHeader">
        <p className="sectionEyebrow">On-Chain State</p>
        <span className="integrityCardTag">Blockchain observation</span>
      </header>
      {!available ? (
        <p className="integrityEmptyNote">
          No on-chain supply observation is stored for this asset. Link a monitoring target so supply is
          observed, then reconciliation can run.
        </p>
      ) : (
        <>
          <Row label="Total Supply"><strong>{formatSupply(state.total_supply)}</strong>{state.total_supply != null ? ' units' : null}</Row>
          <Row label={state.last_delta_operation === 'burn' ? 'Last Burn' : 'Last Mint'}>
            {state.last_delta != null
              ? <strong>{formatVarianceUnits(state.last_delta_operation === 'burn' ? `-${state.last_delta}` : state.last_delta)}</strong>
              : <Unavailable label="Not observed" />}
          </Row>
          <Row label="Last Update">
            {state.observed_at ? relativeTime(state.observed_at) : <Unavailable />}
            {state.stale ? <> <StatusPill label="Stale" variant="warning" /></> : null}
          </Row>
          <Row label="Network">{state.chain_network || <Unavailable />}</Row>
          <Row label="Contract" mono>{state.contract_address ? truncateHex(state.contract_address) : <Unavailable />}</Row>
          <Row label="Block">{state.block_number != null ? String(state.block_number) : <Unavailable label="Not recorded" />}</Row>
          <Row label="Transaction" mono>{state.tx_hash ? truncateHex(state.tx_hash) : <Unavailable label="Not recorded" />}</Row>
          <Row label="Source">
            {state.provider_type || <Unavailable />}
            {state.evidence_source && state.evidence_source !== 'live'
              ? <> <StatusPill label={state.evidence_source === 'simulator' ? 'Simulator' : 'Replay'} variant="warning" /></>
              : null}
          </Row>
        </>
      )}
    </section>
  );
}

/* ── AUTHORITATIVE STATE ───────────────────────────────────────────── */
function AuthoritativeStateCard({ state }: { state: any }) {
  const sourceStatus = String(state?.source_status || 'missing');
  const available = Boolean(state?.available);
  // Freshness is the BACKEND's staleness verdict, not a timestamp the UI judged.
  const freshness = freshnessLabel(state);
  return (
    <section className="integrityCard" aria-label="Authoritative state">
      <header className="integrityCardHeader">
        <p className="sectionEyebrow">Authoritative State</p>
        <span className="integrityCardTag">{state?.source_kind ? String(state.source_kind).replace(/_/g, ' ') : 'Business system of record'}</span>
      </header>
      {sourceStatus === 'missing' ? (
        <>
          {/* Stated as an explicit, labelled "Not configured" — an absent system of
              record is a configuration fact, never an implied clean result. */}
          <Row label="Authoritative source"><Unavailable label="Not configured" /></Row>
          <Row label="Expected Units"><Unavailable /></Row>
          <Row label="Freshness"><StatusPill label={freshness.label} variant={freshness.variant} /></Row>
          <p className="integrityEmptyNote">
            No authoritative off-chain state is recorded for this asset. Without a system of record there is
            nothing to reconcile the chain against.
          </p>
        </>
      ) : (
        <>
          <Row label="Expected Units">
            {available ? <strong>{formatSupply(state.expected_total_supply)}</strong> : <Unavailable />}
          </Row>
          <Row label="Settlement State">
            {state.settlement_state ? <StatusPill label={String(state.settlement_state)} variant={available ? 'info' : 'neutral'} /> : <Unavailable />}
          </Row>
          <Row label="Last Updated">
            {state.observed_at ? relativeTime(state.observed_at) : <Unavailable />}
            {state.stale ? <> <StatusPill label="Stale" variant="warning" /></> : null}
          </Row>
          <Row label="Source">
            {state.source_name || <Unavailable />}
            {state.evidence_source && state.evidence_source !== 'live'
              ? <> <StatusPill label={state.evidence_source === 'simulator' ? 'Simulator' : 'Replay'} variant="warning" /></>
              : null}
          </Row>
          <Row label="Reference" mono>{state.external_reference || <Unavailable label="Not provided" />}</Row>
          <Row label="Freshness"><StatusPill label={freshness.label} variant={freshness.variant} /></Row>
          {sourceStatus !== 'reported' ? (
            <p className="integrityWarnNote" role="status">
              The authoritative source reported <strong>{sourceStatus}</strong>
              {state.source_error ? `: ${state.source_error}` : ''}. Its last known value cannot be treated as current.
            </p>
          ) : null}
        </>
      )}
    </section>
  );
}

/* ── RECONCILIATION RESULT ─────────────────────────────────────────── */
function ReconciliationResultCard({ reconciliation }: { reconciliation: any }) {
  const status = String(reconciliation?.status || '');
  const anomaly = isAnomalyStatus(status);
  const indeterminate = isIndeterminateStatus(status);
  const direction = varianceDirection(reconciliation?.variance_units);
  const tone = anomaly ? 'integrityResultCritical' : indeterminate ? 'integrityResultWarning' : 'integrityResultOk';

  return (
    <section className={`integrityCard integrityResultCard ${tone}`} aria-label="Reconciliation result">
      <header className="integrityCardHeader">
        <p className="sectionEyebrow">Reconciliation Result</p>
        <StatusPill label={String(reconciliation?.severity || 'low')} variant={severityVariant(reconciliation?.severity)} />
      </header>
      <div className="integrityResultGrid">
        <div>
          <span className="integrityKvLabel">Status</span>
          <p className="integrityResultStatus">
            <StatusPill label={reconciliationStatusLabel(status)} variant={reconciliationStatusVariant(status)} />
          </p>
        </div>
        <div>
          <span className="integrityKvLabel">Variance</span>
          <p className={`integrityResultVariance integrityVariance-${direction}`}>
            {formatVarianceUnits(reconciliation?.variance_units)}
          </p>
        </div>
      </div>
      <Row label="Reason" mono>{reasonCodeLabel(reconciliation?.reason_code)}</Row>
      <Row label="Rule" mono>{formatRule(reconciliation?.rule_id, reconciliation?.rule_version)}</Row>
      <Row label="Evidence">{formatEvidenceCount(reconciliation?.evidence_count)}</Row>
      <Row label="Evaluated">{relativeTime(reconciliation?.evaluated_at)}</Row>
      <p className="integrityResultMeaning">{reconciliationStatusMeaning(status)}</p>
    </section>
  );
}

/* ── AI ASSET RISK ASSESSOR ────────────────────────────────────────── */
function AiAssessorCard({
  assessment, cta, onInvestigate, investigating, actionError,
}: {
  assessment: any;
  cta: ReturnType<typeof investigateCta>;
  onInvestigate: () => void;
  investigating: boolean;
  actionError: string;
}) {
  return (
    <section className="integrityCard integrityAiCard" aria-label="AI Asset Risk Assessor">
      <header className="integrityCardHeader">
        <p className="sectionEyebrow">AI Asset Risk Assessor</p>
        <StatusPill
          label={assessment?.source === 'ai' ? 'AI narrative' : 'Deterministic narrative'}
          variant={assessment?.source === 'ai' ? 'info' : 'neutral'}
        />
      </header>
      <p className="integrityAiText">
        {assessment?.explanation || 'No reconciliation result has been produced for this asset yet.'}
      </p>
      {assessment?.risk_impact ? (
        <Row label="Risk Impact"><strong>{assessment.risk_impact}</strong></Row>
      ) : null}
      {Array.isArray(assessment?.next_steps) && assessment.next_steps.length > 0 ? (
        <ul className="integrityAiSteps">
          {assessment.next_steps.map((step: string, i: number) => <li key={i}>{step}</li>)}
        </ul>
      ) : null}
      <p className="integrityAiBoundary">
        The reconciliation engine computed the supply, variance, reason code and severity. This narrative only
        explains that result — it cannot change it.
      </p>
      {actionError ? <p className="statusLine" role="alert">{actionError}</p> : null}
      <div className="integrityActions">
        {cta.destination && !investigating ? (
          <Link href={cta.destination} prefetch={false} className="btn btn-primary" title={cta.hint}>{cta.label}</Link>
        ) : (
          <button
            type="button"
            className="btn btn-primary"
            disabled={!cta.enabled || investigating}
            aria-busy={investigating}
            title={cta.hint}
            onClick={onInvestigate}
          >
            {investigating ? 'Opening investigation…' : cta.label}
          </button>
        )}
      </div>
    </section>
  );
}

/* ── History ───────────────────────────────────────────────────────── */
function HistoryView({ assetId }: { assetId: string }) {
  const { authHeaders } = usePilotAuth();
  const [rows, setRows] = useState<any[] | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setError('');
      try {
        const response = await fetch(`/api/assets/${assetId}/integrity/history`, { headers: { ...authHeaders() }, cache: 'no-store' });
        if (!response.ok) {
          if (!cancelled) setError('Reconciliation history is unavailable right now.');
        } else {
          const payload = await response.json();
          if (!cancelled) setRows(Array.isArray(payload?.snapshots) ? payload.snapshots : []);
        }
      } catch {
        if (!cancelled) setError('Reconciliation history is temporarily unavailable.');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [assetId, authHeaders]);

  if (loading) return <div className="assetsTableSkeleton"><div className="skelBlock" style={{ height: '120px' }} /></div>;
  if (error) return <p className="statusLine" role="alert">{error}</p>;
  if (!rows || rows.length === 0) {
    return <p className="integrityEmptyNote">No reconciliation snapshots have been recorded for this asset yet.</p>;
  }
  return (
    <div className="integrityHistoryWrap">
      <table className="integrityHistoryTable">
        <thead>
          <tr>
            <th>Evaluated</th><th>Observed</th><th>Expected</th><th>Variance</th>
            <th>Status</th><th>Reason</th><th>Rule</th><th>Evidence</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id}>
              <td>{relativeTime(row.evaluated_at)}</td>
              <td>{formatSupply(row.observed_supply)}</td>
              <td>{formatSupply(row.expected_supply)}</td>
              <td className={`integrityVariance-${varianceDirection(row.variance_units)}`}>{formatVarianceUnits(row.variance_units)}</td>
              <td><StatusPill label={reconciliationStatusLabel(row.status)} variant={reconciliationStatusVariant(row.status)} /></td>
              <td className="integrityMono">{reasonCodeLabel(row.reason_code)}</td>
              <td className="integrityMono">{formatRule(row.rule_id, row.rule_version)}</td>
              <td>{formatEvidenceCount(row.evidence_count)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ── Panel ─────────────────────────────────────────────────────────── */
export default function AssetIntegrityPanel({ assetId, view = 'integrity' }: Props) {
  const { authHeaders } = usePilotAuth();
  const [payload, setPayload] = useState<any | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [actionError, setActionError] = useState('');
  const [investigating, setInvestigating] = useState(false);
  const [reconciling, setReconciling] = useState(false);

  const load = useCallback(async () => {
    setError('');
    try {
      const response = await fetch(`/api/assets/${assetId}/integrity`, { headers: { ...authHeaders() }, cache: 'no-store' });
      if (!response.ok) {
        setPayload(null);
        setError('Asset integrity state is unavailable right now.');
        return;
      }
      setPayload(await response.json());
    } catch {
      setPayload(null);
      setError('Asset integrity state is temporarily unavailable.');
    } finally {
      setLoading(false);
    }
  }, [assetId, authHeaders]);

  useEffect(() => { setLoading(true); void load(); }, [load]);

  const panelState = integrityPanelState(payload, { loading, error });
  const cta = useMemo(() => investigateCta(payload), [payload]);

  // Opens (or returns) the existing investigation. Repeated clicks are guarded
  // locally AND idempotent on the backend, so no duplicate incident is created.
  const investigate = useCallback(async () => {
    if (investigating || !cta.enabled) return;
    setInvestigating(true);
    setActionError('');
    try {
      const response = await fetch(`/api/assets/${assetId}/integrity/investigate`, {
        method: 'POST', headers: { ...authHeaders() }, cache: 'no-store',
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) {
        setActionError(typeof body?.detail === 'string' ? body.detail : 'Could not open an investigation for this variance.');
        return;
      }
      await load();
      if (typeof body?.destination === 'string' && body.destination) {
        window.location.href = body.destination;
      }
    } catch {
      setActionError('Could not reach the investigation service.');
    } finally {
      setInvestigating(false);
    }
  }, [assetId, authHeaders, cta.enabled, investigating, load]);

  const reconcile = useCallback(async () => {
    if (reconciling) return;
    setReconciling(true);
    setActionError('');
    try {
      const response = await fetch(`/api/assets/${assetId}/integrity/reconcile`, {
        method: 'POST', headers: { ...authHeaders() }, cache: 'no-store',
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        const detail = typeof body?.detail === 'string' ? body.detail : body?.detail?.message;
        setActionError(detail || 'Reconciliation could not be run right now.');
        return;
      }
      await load();
    } catch {
      setActionError('Could not reach the reconciliation service.');
    } finally {
      setReconciling(false);
    }
  }, [assetId, authHeaders, load, reconciling]);

  if (view === 'history') return <HistoryView assetId={assetId} />;

  if (panelState === 'loading') {
    return (
      <div className="assetsTableSkeleton" aria-busy="true" aria-label="Loading asset integrity">
        <div className="skelBlock" style={{ height: '110px' }} />
        <div className="skelBlock" style={{ height: '140px' }} />
      </div>
    );
  }
  if (panelState === 'error') {
    return <p className="statusLine" role="alert">{error || 'Asset integrity state is unavailable right now.'}</p>;
  }

  const onchain = payload?.onchain_state ?? null;
  const authoritative = payload?.authoritative_state ?? null;

  if (view === 'onchain') return <OnChainStateCard state={onchain} />;
  if (view === 'offchain') return <AuthoritativeStateCard state={authoritative} />;

  return (
    <div className="integrityLayout">
      <div className="integrityStateGrid">
        <OnChainStateCard state={onchain} />
        <AuthoritativeStateCard state={authoritative} />
      </div>

      {panelState === 'not_configured' ? (
        <p className="integrityEmptyNote">
          Operational integrity is not configured for this asset. Reconciliation needs both an on-chain supply
          observation and an authoritative off-chain state to compare it against.
        </p>
      ) : panelState === 'not_evaluated' ? (
        <p className="integrityEmptyNote">
          No reconciliation has been recorded for this asset yet. Nothing here asserts that the asset is healthy.
        </p>
      ) : (
        <div className="integrityResultGridOuter">
          <ReconciliationResultCard reconciliation={payload.reconciliation} />
          <AiAssessorCard
            assessment={payload.ai_assessment}
            cta={cta}
            onInvestigate={investigate}
            investigating={investigating}
            actionError={actionError}
          />
        </div>
      )}

      <div className="integrityFooter">
        {payload?.reconcile_enabled ? (
          <button type="button" className="btn btn-secondary" disabled={reconciling} aria-busy={reconciling} onClick={reconcile}>
            {reconciling ? 'Reconciling…' : 'Run reconciliation'}
          </button>
        ) : null}
        <span className="integrityFooterNote">
          Reconciliation policy {formatRule(payload?.rule?.rule_id, payload?.rule?.rule_version)}. Viewing this
          tab reads stored results only — it never creates one.
        </span>
      </div>
    </div>
  );
}
