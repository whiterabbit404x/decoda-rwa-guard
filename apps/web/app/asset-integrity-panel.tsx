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
  absentValueLabel,
  assessorCta,
  assessorView,
  authoritativeApplicabilityRow,
  authoritativeCardState,
  availabilityLabel,
  formatEvidenceCount,
  formatRule,
  formatSupply,
  formatVarianceUnits,
  freshnessLabel,
  integrityBanner,
  integrityPanelState,
  onchainAvailability,
  reasonCodeLabel,
  reconcileAction,
  reconciliationResultTone,
  reconciliationStatusLabel,
  reconciliationMeaning,
  reconciliationStatusVariant,
  reconciliationView,
  relativeTime,
  riskImpactAbsentLabel,
  severityVariant,
  tokenSupplyApplicability,
  truncateHex,
  varianceDirection,
} from './asset-integrity-presentation';
import type { AuthoritativeRequirement, Availability, ReconciliationView } from './asset-integrity-presentation';

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

/**
 * A field with no value, stating the REASON. "Not applicable" (a wallet has no
 * token supply) is a different fact from "Unavailable" (we could not read it) and
 * from "Not configured" (nothing is set up to read it) — collapsing them would
 * report a collection failure that never happened.
 */
function Absent({ availability }: { availability: string }) {
  return <span className="integrityUnavailable">{absentValueLabel(availability)}</span>;
}

/**
 * A genuine request failure, stated ABOVE the workspace. It never replaces the
 * panels: an API outage is not evidence about the asset, so hiding the panels
 * behind it would turn a transport problem into an (absent) verdict.
 */
function ErrorBanner({
  banner, onRetry,
}: { banner: ReturnType<typeof integrityBanner>; onRetry: () => void }) {
  if (!banner) return null;
  return (
    <div className="integrityErrorBanner" role="alert">
      <p className="integrityErrorBannerTitle">{banner.message}</p>
      <p className="integrityErrorBannerDetail">{banner.detail}</p>
      <button type="button" className="btn btn-ghost" onClick={onRetry}>Retry</button>
    </div>
  );
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
/**
 * Always renders every row, in two kinds:
 *   * REGISTRY facts (asset type, network, address) — what the workspace
 *     registered. Always known, and never used to fill in an observation the
 *     chain did not provide.
 *   * OBSERVATION facts (supply, last mint/burn, last observed, provider) — each
 *     states its own reason when absent, behind the Observation badge.
 *
 * A wallet has no token total supply, so that field reads "Not applicable" rather
 * than "Unavailable" — the latter would claim a read we never attempted.
 */
function OnChainStateCard({ state, asset }: { state: any; asset?: any }) {
  const availability = onchainAvailability(state);
  const observed = availability === 'AVAILABLE' || availability === 'STALE';
  const supplyApplies = tokenSupplyApplicability(state) === 'APPLICABLE';
  const badge = availabilityLabel(availability);
  const assetType = state?.rwa_asset_type || state?.asset_type || asset?.rwa_asset_type || asset?.asset_type;
  const network = state?.chain_network || state?.asset_chain_network || asset?.chain_network;
  const address = state?.contract_address || state?.asset_address || asset?.token_contract_address || asset?.identifier;

  return (
    <section className="integrityCard" aria-label="On-chain state">
      <header className="integrityCardHeader">
        <p className="sectionEyebrow">On-Chain State</p>
        <span className="integrityCardTag">Blockchain observation</span>
      </header>

      <Row label="Asset Type">{assetType ? String(assetType).replace(/[-_]/g, ' ') : <Absent availability="UNKNOWN" />}</Row>
      <Row label="Network">{network || <Absent availability="NOT_CONFIGURED" />}</Row>
      <Row label="Address" mono>{address ? truncateHex(String(address)) : <Absent availability="NOT_CONFIGURED" />}</Row>
      <Row label="Observation"><StatusPill label={badge.label} variant={badge.variant} /></Row>

      <Row label="Total Supply">
        {!supplyApplies
          ? <Absent availability="NOT_APPLICABLE" />
          : state?.total_supply != null
            ? <><strong>{formatSupply(state.total_supply)}</strong> units</>
            : <Absent availability={availability} />}
      </Row>
      <Row label={state?.last_delta_operation === 'burn' ? 'Last Burn' : 'Last Mint'}>
        {!supplyApplies
          ? <Absent availability="NOT_APPLICABLE" />
          : state?.last_delta != null
            ? <strong>{formatVarianceUnits(state.last_delta_operation === 'burn' ? `-${state.last_delta}` : state.last_delta)}</strong>
            : <Unavailable label="Not observed" />}
      </Row>
      <Row label="Last Observed">
        {state?.observed_at ? relativeTime(state.observed_at) : <Absent availability={availability} />}
        {state?.stale ? <> <StatusPill label="Stale" variant="warning" /></> : null}
      </Row>
      <Row label="Block">{state?.block_number != null ? String(state.block_number) : <Unavailable label="Not recorded" />}</Row>
      <Row label="Transaction" mono>{state?.tx_hash ? truncateHex(state.tx_hash) : <Unavailable label="Not recorded" />}</Row>
      <Row label="Provider">
        {state?.provider_type || <Absent availability={availability} />}
        {state?.evidence_source && state.evidence_source !== 'live'
          ? <> <StatusPill label={state.evidence_source === 'simulator' ? 'Simulator' : 'Replay'} variant="warning" /></>
          : null}
      </Row>

      {/* A wallet address has no token total supply, so telling the operator to
          link a target "so supply is observed" would promise a fix for a gap that
          cannot close. Each case states only what is true of it. */}
      {!supplyApplies ? (
        <p className="integrityEmptyNote">
          This asset has no token total supply, so supply reconciliation does not apply to it. Register a
          token contract if it should be reconciled against one.
        </p>
      ) : !observed ? (
        <p className="integrityEmptyNote">
          No on-chain supply observation is stored for this asset. Link a monitoring target so supply is
          observed, then reconciliation can run.
        </p>
      ) : null}
    </section>
  );
}

/* ── AUTHORITATIVE STATE ───────────────────────────────────────────── */
/**
 * Always renders every row. An absent system of record is a CONFIGURATION fact
 * ("Not configured"), a source that failed is a TRANSIENT fact ("Source
 * unavailable"), and neither is ever an implied clean result.
 *
 * A third case is neither: when supply reconciliation does not apply to the
 * asset, no authoritative ledger is REQUIRED, so the rows read "Not applicable"
 * and the summary row states applicability instead of availability. "Not
 * configured" there would be a to-do the operator can never usefully complete.
 */
function AuthoritativeStateCard({
  state, card,
}: { state: any; card: { availability: Availability; requirement: AuthoritativeRequirement } }) {
  const availability = card.availability;
  const notRequired = card.requirement === 'NOT_REQUIRED' && availability === 'NOT_APPLICABLE';
  const configured = availability !== 'NOT_CONFIGURED' && availability !== 'UNKNOWN' && !notRequired;
  const reported = availability === 'AVAILABLE' || availability === 'STALE';
  const sourceStatus = String(state?.source_status || 'missing').toLowerCase();
  // Freshness is the BACKEND's staleness verdict, not a timestamp the UI judged.
  const freshness = freshnessLabel(state, availability);
  const summary = authoritativeApplicabilityRow(card);

  return (
    <section className="integrityCard" aria-label="Authoritative state">
      <header className="integrityCardHeader">
        <p className="sectionEyebrow">Authoritative State</p>
        <span className="integrityCardTag">{state?.source_kind ? String(state.source_kind).replace(/_/g, ' ') : 'Business system of record'}</span>
      </header>

      <Row label="Source">
        {state?.source_name || <Absent availability={availability} />}
        {state?.evidence_source && state.evidence_source !== 'live'
          ? <> <StatusPill label={state.evidence_source === 'simulator' ? 'Simulator' : 'Replay'} variant="warning" /></>
          : null}
      </Row>
      <Row label="Expected Units">
        {reported && state?.expected_total_supply != null
          ? <strong>{formatSupply(state.expected_total_supply)}</strong>
          : <Absent availability={configured ? 'SOURCE_UNAVAILABLE' : availability} />}
      </Row>
      <Row label="Settlement State">
        {state?.settlement_state
          ? <StatusPill label={String(state.settlement_state)} variant={reported ? 'info' : 'neutral'} />
          : <Absent availability={availability} />}
      </Row>
      <Row label="Reference" mono>{state?.external_reference || <Unavailable label="—" />}</Row>
      <Row label="Last Updated">
        {state?.observed_at ? relativeTime(state.observed_at) : <Unavailable label="—" />}
        {state?.stale ? <> <StatusPill label="Stale" variant="warning" /></> : null}
      </Row>
      <Row label="Freshness"><StatusPill label={freshness.label} variant={freshness.variant} /></Row>
      <Row label={summary.label}><StatusPill label={summary.value} variant={summary.variant} /></Row>

      {notRequired ? (
        <p className="integrityEmptyNote">
          No authoritative supply ledger is required because supply reconciliation does not apply to this
          asset. Nothing is missing here.
        </p>
      ) : !configured ? (
        <p className="integrityEmptyNote">
          No authoritative off-chain state is recorded for this asset. Without a system of record there is
          nothing to reconcile the chain against.
        </p>
      ) : !reported ? (
        <p className="integrityWarnNote" role="status">
          The authoritative source reported <strong>{sourceStatus}</strong>
          {state?.source_error ? `: ${state.source_error}` : ''}. Its last known value cannot be treated as current.
        </p>
      ) : null}
    </section>
  );
}

/* ── RECONCILIATION RESULT ─────────────────────────────────────────── */
/**
 * Always renders. When `evaluated` is false no reconciliation has been recorded,
 * so the card names the blocking state and shows NO variance, severity or rule:
 * with no authoritative baseline there is nothing to subtract, and reporting a
 * variance anyway would fabricate the very anomaly this screen exists to detect.
 */
function ReconciliationResultCard({ view }: { view: ReconciliationView }) {
  const direction = varianceDirection(view.variance_units);
  // Green is reserved for a RECORDED result that says the asset reconciles; an
  // unevaluated card is never green, whatever status a payload claims. Not
  // applicable is neutral rather than amber — there is no gap to chase.
  const tone = reconciliationResultTone(view);

  return (
    <section className={`integrityCard integrityResultCard ${tone}`} aria-label="Reconciliation result">
      <header className="integrityCardHeader">
        <p className="sectionEyebrow">Reconciliation Result</p>
        {view.evaluated && view.severity
          ? <StatusPill label={String(view.severity)} variant={severityVariant(view.severity)} />
          : <StatusPill label="Not evaluated" variant="neutral" />}
      </header>
      <div className="integrityResultGrid">
        <div>
          <span className="integrityKvLabel">Status</span>
          <p className="integrityResultStatus">
            <StatusPill label={reconciliationStatusLabel(view.status)} variant={reconciliationStatusVariant(view.status)} />
          </p>
        </div>
        <div>
          <span className="integrityKvLabel">Variance</span>
          <p className={`integrityResultVariance integrityVariance-${direction}`}>
            {view.evaluated ? formatVarianceUnits(view.variance_units) : 'Not calculated'}
          </p>
        </div>
      </div>
      <Row label="Reason" mono>{reasonCodeLabel(view.reason_code)}</Row>
      <Row label="Rule" mono>{view.evaluated ? formatRule(view.rule_id, view.rule_version) : 'Not evaluated'}</Row>
      <Row label="Evidence">{formatEvidenceCount(view.evidence_count)}</Row>
      <Row label="Evaluated">{view.evaluated ? relativeTime(view.evaluated_at) : <Unavailable label="Never" />}</Row>
      <p className="integrityResultMeaning">{reconciliationMeaning(view)}</p>
    </section>
  );
}

/* ── AI ASSET RISK ASSESSOR ────────────────────────────────────────── */
/**
 * Always renders, with or without AI. When reconciliation could not run there is
 * nothing for a model to explain, so the narrative is deterministic and the model
 * is never asked to infer a value the engine did not produce.
 */
function AiAssessorCard({
  assessment, cta, status, onInvestigate, investigating, actionError,
}: {
  assessment: ReturnType<typeof assessorView>;
  cta: ReturnType<typeof assessorCta>;
  /** The deterministic reconciliation status this narrative explains. */
  status: string;
  onInvestigate: () => void;
  investigating: boolean;
  actionError: string;
}) {
  return (
    <section className="integrityCard integrityAiCard" aria-label="AI Asset Risk Assessor">
      <header className="integrityCardHeader">
        <p className="sectionEyebrow">AI Asset Risk Assessor</p>
        <StatusPill
          label={assessment.source === 'ai' ? 'AI narrative' : 'Deterministic narrative'}
          variant={assessment.source === 'ai' ? 'info' : 'neutral'}
        />
      </header>
      <Row label="Assessment">
        <StatusPill label={assessment.assessment} variant={assessment.assessment === 'Complete' ? 'info' : 'neutral'} />
      </Row>
      {assessment.assessment_reason
        ? <Row label="Reason" mono>{reasonCodeLabel(assessment.assessment_reason)}</Row>
        : null}
      <Row label="Risk Impact">
        {assessment.risk_impact
          ? <strong>{assessment.risk_impact}</strong>
          : <Unavailable label={riskImpactAbsentLabel(status)} />}
      </Row>
      <p className="integrityAiText">{assessment.explanation}</p>
      {assessment.next_steps.length > 0 ? (
        <ul className="integrityAiSteps">
          {assessment.next_steps.map((step, i) => <li key={i}>{step}</li>)}
        </ul>
      ) : null}
      <p className="integrityAiBoundary">
        The reconciliation engine computed the supply, variance, reason code and severity. This narrative only
        explains that result — it cannot change it.
      </p>
      {actionError ? <p className="statusLine" role="alert">{actionError}</p> : null}
      <div className="integrityActions">
        {/* Investigate Variance appears ONLY for an evidenced, persisted variance.
            With no baseline there is no variance to investigate, so the actionable
            step is the EXISTING Monitoring Sources workflow instead. */}
        {cta.kind === 'none' ? (
          <span className="integrityFooterNote">{cta.hint}</span>
        ) : cta.destination && !investigating ? (
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
  const [httpStatus, setHttpStatus] = useState<number | null>(null);
  const [actionError, setActionError] = useState('');
  const [investigating, setInvestigating] = useState(false);
  const [reconciling, setReconciling] = useState(false);

  const load = useCallback(async () => {
    setError('');
    try {
      const response = await fetch(`/api/assets/${assetId}/integrity`, { headers: { ...authHeaders() }, cache: 'no-store' });
      if (!response.ok) {
        // A failed request is an ERROR, not a fact about the asset. The payload is
        // cleared so nothing stale is shown as current, and the workspace below
        // renders in its unknown state rather than disappearing.
        setPayload(null);
        setHttpStatus(response.status);
        setError('Asset integrity state is unavailable right now.');
        return;
      }
      setHttpStatus(null);
      setPayload(await response.json());
    } catch {
      setPayload(null);
      setHttpStatus(null);
      setError('Asset integrity state is temporarily unavailable.');
    } finally {
      setLoading(false);
    }
  }, [assetId, authHeaders]);

  useEffect(() => { setLoading(true); void load(); }, [load]);

  const panelState = integrityPanelState(payload, { loading, error });
  const banner = integrityBanner({ loading, error, httpStatus });
  // Built for EVERY payload, including a null one, so the four panels always have
  // something truthful to render.
  const recon = useMemo(() => reconciliationView(payload), [payload]);
  const assessment = useMemo(() => assessorView(payload, recon), [payload, recon]);
  const cta = useMemo(() => assessorCta(payload, recon), [payload, recon]);
  // Applicability is resolved from the SAME canonical fact as the verdict, so
  // the Authoritative card can never say "Not configured" while the result card
  // says the dimension does not apply.
  const authoritativeCard = useMemo(() => authoritativeCardState(payload), [payload]);
  const runReconciliation = useMemo(() => reconcileAction(payload, recon), [payload, recon]);

  // Opens (or returns) the existing investigation. Repeated clicks are guarded
  // locally AND idempotent on the backend, so no duplicate incident is created.
  const investigate = useCallback(async () => {
    if (investigating || cta.kind !== 'investigate' || !cta.enabled) return;
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
  }, [assetId, authHeaders, cta.enabled, cta.kind, investigating, load]);

  const reconcile = useCallback(async () => {
    // Guarded here as well as on the button: reconciliation that cannot produce a
    // verdict is never started, however the click arrives.
    if (reconciling || !runReconciliation.enabled) return;
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
  }, [assetId, authHeaders, load, reconciling, runReconciliation.enabled]);

  const retry = useCallback(() => { setLoading(true); void load(); }, [load]);

  if (view === 'history') return <HistoryView assetId={assetId} />;

  if (panelState === 'loading') {
    return (
      <div className="assetsTableSkeleton" aria-busy="true" aria-label="Loading asset integrity">
        <div className="skelBlock" style={{ height: '110px' }} />
        <div className="skelBlock" style={{ height: '140px' }} />
      </div>
    );
  }

  const onchain = payload?.onchain_state ?? null;
  const authoritative = payload?.authoritative_state ?? null;
  const asset = payload?.asset ?? null;

  // The single-card views degrade the same way: the card still renders, with the
  // failure stated above it rather than in place of it.
  if (view === 'onchain') {
    return (
      <div className="integrityLayout">
        <ErrorBanner banner={banner} onRetry={retry} />
        <OnChainStateCard state={onchain} asset={asset} />
      </div>
    );
  }
  if (view === 'offchain') {
    return (
      <div className="integrityLayout">
        <ErrorBanner banner={banner} onRetry={retry} />
        <AuthoritativeStateCard state={authoritative} card={authoritativeCard} />
      </div>
    );
  }

  // The Integrity workspace ALWAYS renders its four panels. Missing, stale,
  // unavailable, not-applicable and never-evaluated are all states the panels
  // state explicitly — none of them is a reason to hide the workspace, and a
  // failed request only adds the banner above it.
  return (
    <div className="integrityLayout">
      <ErrorBanner banner={banner} onRetry={retry} />

      <div className="integrityStateGrid">
        <OnChainStateCard state={onchain} asset={asset} />
        <AuthoritativeStateCard state={authoritative} card={authoritativeCard} />
      </div>

      <div className="integrityResultGridOuter">
        <ReconciliationResultCard view={recon} />
        <AiAssessorCard
          assessment={assessment}
          cta={cta}
          status={recon.status}
          onInvestigate={investigate}
          investigating={investigating}
          actionError={actionError}
        />
      </div>

      {/* Run reconciliation stays visible but DISABLED where reconciliation
          cannot produce a verdict — running it could only ever re-record "not
          applicable", so offering it live would imply a verdict is one click
          away. The reason is stated beside it rather than left to a tooltip. */}
      <div className="integrityFooter">
        {runReconciliation.visible ? (
          <button
            type="button"
            className="btn btn-secondary"
            disabled={reconciling || !runReconciliation.enabled}
            aria-busy={reconciling}
            title={runReconciliation.hint}
            onClick={reconcile}
          >
            {reconciling ? 'Reconciling…' : runReconciliation.label}
          </button>
        ) : null}
        {runReconciliation.visible && !runReconciliation.enabled ? (
          <span className="integrityFooterNote">{runReconciliation.hint}</span>
        ) : null}
        <span className="integrityFooterNote">
          Reconciliation policy {formatRule(payload?.rule?.rule_id, payload?.rule?.rule_version)}. Viewing this
          tab reads stored results only — it never creates one.
        </span>
      </div>
    </div>
  );
}
