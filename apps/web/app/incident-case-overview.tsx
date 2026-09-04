'use client';

import Link from 'next/link';

import { StatusPill } from './components/ui-primitives';
import {
  caseSectionRecorded,
  caseStateLabel,
  caseStateVariant,
  formatCaseAmount,
  formatForensicDate,
  humanizeToken,
  policyDecisionVariant,
  responseStateVariant,
  snapshotStatusLabel,
  snapshotStatusVariant,
  summarizeResponseState,
  type CaseResponseAction,
  type ForensicLoadState,
  type IncidentCaseSummary,
} from './incident-forensics-presentation';

/**
 * Screen 7 — Overview: the deterministic executive summary of one case.
 *
 * It answers, in order and from persisted records only: what was detected, what
 * the chain recorded, what the operational systems of record said, what the
 * deterministic policy engine decided, where the response stands, and what
 * evidence exists to prove it.
 *
 * Every section fails closed. A section whose record is absent says "Not
 * recorded"; a reconciliation that could not establish truth says so and is
 * styled neutrally — never as reconciled, never as an anomaly. No AI-generated
 * text is allowed into any of these fields: the AI Investigation tab explains,
 * this tab states facts.
 *
 * Layout only differs by where it is rendered. `wide` (the full-investigation
 * workspace) lays the record out the way the investigation actually ran — the
 * chain reading beside the operational reading, their reconciliation result
 * beneath them, then the policy verdict — with the case state (response,
 * evidence) in its own column. `narrow` keeps the single-column stack. Neither
 * layout changes a single fact.
 */
export default function IncidentCaseOverview({ summary, load, responseActions, responseLoad, incidentId, layout = 'narrow' }: {
  summary: IncidentCaseSummary | null;
  load: ForensicLoadState;
  responseActions: readonly CaseResponseAction[];
  /** Whether Screen 8's action records have been read. "No response action" is a
   *  claim about Screen 8's data, so it waits for that read to settle. */
  responseLoad: ForensicLoadState;
  incidentId: string;
  /** 'narrow' = a single column at every width;
   *  'wide' = the full-investigation content area, which fits the analysis /
   *  case-state split. */
  layout?: 'narrow' | 'wide';
}) {
  if (load === 'idle' || load === 'loading') {
    return <p className="muted" style={{ fontSize: '0.85rem' }} aria-busy="true">Loading case summary…</p>;
  }
  if (load === 'unauthorized') {
    return (
      <p className="muted" style={{ fontSize: '0.85rem' }} role="alert">
        You do not have permission to view this incident&apos;s case record in the current workspace.
      </p>
    );
  }
  if (load === 'not_found') {
    return <p className="muted" style={{ fontSize: '0.85rem' }} role="alert">This incident could not be found in the current workspace.</p>;
  }
  if (load === 'error' || !summary) {
    return (
      <p className="muted" style={{ fontSize: '0.85rem' }} role="alert">
        Case summary unavailable — the incident record could not be read. No partial summary is shown as the complete case.
      </p>
    );
  }

  const detection = summary.detection ?? {};
  const onChain = summary.on_chain ?? {};
  const operational = summary.operational ?? {};
  const policy = summary.policy ?? {};
  const evidence = summary.evidence ?? {};
  const response = summarizeResponseState(responseActions);
  const responseKnown = responseLoad === 'ready' || responseLoad === 'empty';

  const detectionSection = <DetectionSection detection={detection} />;
  const onChainSection = <OnChainSection onChain={onChain} />;
  const operationalSection = <OperationalSection operational={operational} />;
  const policySection = <PolicySection policy={policy} />;
  const responseSection = (
    <ResponseSection response={response} responseKnown={responseKnown} responseLoad={responseLoad} incidentId={incidentId} />
  );
  const evidenceSection = <EvidenceSection evidence={evidence} />;

  if (layout === 'wide') {
    return (
      <div className="incidentCaseOverview incidentCaseOverview-wide" aria-label="Incident case summary">
        {/* Left: how cryptographic validity and operational authorization came apart. */}
        <div className="incidentCaseAnalysis">
          <p className="incidentCaseColumnTitle">Operational integrity analysis</p>
          {detectionSection}
          <div className="incidentCaseCompare">
            {onChainSection}
            {operationalSection}
          </div>
          <ReconciliationResult operational={operational} />
          {policySection}
        </div>
        {/* Right: where the case stands now. Screens 8 and 9 own changing it. */}
        <div className="incidentCaseStateColumn">
          <p className="incidentCaseColumnTitle">Case state</p>
          {responseSection}
          {evidenceSection}
        </div>
      </div>
    );
  }

  return (
    <div className="incidentCaseOverview" aria-label="Incident case summary">
      {detectionSection}
      {onChainSection}
      {operationalSection}
      {policySection}
      {responseSection}
      {evidenceSection}
    </div>
  );
}

/* ── What was detected ──────────────────────────────────────────────── */
function DetectionSection({ detection }: { detection: NonNullable<IncidentCaseSummary['detection']> }) {
  return (
    <CaseSection title="Detection">
      {detection.category || detection.detection_type ? (
        <>
          {detection.category ? <CaseLine label="Category" value={humanizeToken(detection.category) ?? '—'} /> : null}
          {detection.detection_type ? (
            <CaseLine label="Type" value={detection.title ?? humanizeToken(detection.detection_type) ?? '—'} />
          ) : null}
          {detection.reason_code ? (
            <CaseLine label="Reason code" value={<span className="incidentReasonCode">{detection.reason_code}</span>} />
          ) : null}
          {detection.detected_at ? <CaseLine label="Detected" value={formatForensicDate(detection.detected_at)} /> : null}
        </>
      ) : (
        <p className="muted" style={{ margin: 0, fontSize: '0.82rem' }}>
          No detection is linked to this incident.
        </p>
      )}
    </CaseSection>
  );
}

/* ── What the chain recorded ────────────────────────────────────────── */
function OnChainSection({ onChain }: { onChain: NonNullable<IncidentCaseSummary['on_chain']> }) {
  const observedAmount = formatCaseAmount(onChain.observed_amount);
  return (
    <CaseSection title="On-chain state" state={onChain.state}>
      {caseSectionRecorded(onChain.state) ? (
        <>
          {onChain.operation || observedAmount ? (
            <CaseLine
              label="Observed"
              value={[humanizeToken(onChain.operation), observedAmount].filter(Boolean).join(' ') || '—'}
            />
          ) : null}
          {onChain.tx_hash ? (
            <CaseLine label="Transaction" value={<span className="incidentMonoValue" title={onChain.tx_hash}>{onChain.tx_hash}</span>} />
          ) : null}
          {onChain.block_number ? <CaseLine label="Block" value={onChain.block_number} /> : null}
          {onChain.observed_at ? <CaseLine label="Observed at" value={formatForensicDate(onChain.observed_at)} /> : null}
          {onChain.source ? <CaseLine label="Source" value={onChain.source} /> : null}
        </>
      ) : (
        <p className="muted" style={{ margin: 0, fontSize: '0.82rem' }}>
          No chain observation is recorded for this incident.
        </p>
      )}
    </CaseSection>
  );
}

/* ── What the systems of record said ────────────────────────────────── */
function OperationalSection({ operational }: { operational: NonNullable<IncidentCaseSummary['operational']> }) {
  const expectedAmount = formatCaseAmount(operational.expected_amount);
  const varianceAmount = formatCaseAmount(operational.variance_amount);
  return (
    <CaseSection title="Operational state" state={operational.state}>
      {caseSectionRecorded(operational.state) ? (
        <>
          {operational.reconciliation_status ? (
            <CaseLine label="Reconciliation" value={humanizeToken(operational.reconciliation_status) ?? '—'} />
          ) : null}
          {operational.reason_code ? (
            <CaseLine label="Reason code" value={<span className="incidentReasonCode">{operational.reason_code}</span>} />
          ) : null}
          {expectedAmount ? <CaseLine label="Authorized / expected" value={expectedAmount} /> : null}
          {varianceAmount ? <CaseLine label="Variance" value={varianceAmount} /> : null}
          {operational.authoritative_source ? <CaseLine label="System of record" value={operational.authoritative_source} /> : null}
          {operational.evaluated_at ? <CaseLine label="Evaluated" value={formatForensicDate(operational.evaluated_at)} /> : null}
        </>
      ) : (
        <p className="muted" style={{ margin: 0, fontSize: '0.82rem' }}>
          No operational record has been reconciled against this event.
        </p>
      )}
    </CaseSection>
  );
}

/**
 * The reconciliation verdict between the two readings above, restated from the
 * operational record's OWN fields (`state` + `reconciliation_status`). Nothing is
 * recomputed in the browser: an unreconciled case says so and stays neutral —
 * "could not be established" is never rendered as agreement.
 */
function ReconciliationResult({ operational }: { operational: NonNullable<IncidentCaseSummary['operational']> }) {
  const recorded = caseSectionRecorded(operational.state);
  const variance = formatCaseAmount(operational.variance_amount);
  return (
    <div className="incidentCaseReconciliation" aria-label="Reconciliation result">
      <span className="incidentCaseReconciliationLabel">Reconciliation result</span>
      {recorded ? (
        <>
          <StatusPill
            label={humanizeToken(operational.reconciliation_status) ?? caseStateLabel(operational.state)}
            variant={caseStateVariant(operational.state)}
          />
          {variance ? <span className="tableMeta" style={{ fontSize: '0.72rem' }}>Variance {variance}</span> : null}
        </>
      ) : (
        <span className="muted" style={{ fontSize: '0.8rem' }}>Not reconciled</span>
      )}
    </div>
  );
}

/* ── What the deterministic engine decided ──────────────────────────── */
function PolicySection({ policy }: { policy: NonNullable<IncidentCaseSummary['policy']> }) {
  return (
    <CaseSection title="Policy" state={policy.state}>
      {caseSectionRecorded(policy.state) ? (
        <>
          <CaseLine
            label="Decision"
            value={<StatusPill label={policy.decision ?? 'No decision recorded'} variant={policyDecisionVariant(policy.decision)} />}
          />
          {policy.policy_key ? (
            <CaseLine
              label="Policy"
              value={
                <span className="incidentMonoValue">
                  {policy.policy_key}
                  {policy.policy_version !== null && policy.policy_version !== undefined ? ` v${policy.policy_version}` : ''}
                </span>
              }
            />
          ) : null}
          {policy.evaluation_id ? (
            <CaseLine label="Evaluation" value={<span className="incidentMonoValue" title={policy.evaluation_id}>{policy.evaluation_id}</span>} />
          ) : null}
          {(policy.reason_codes ?? []).length > 0 ? (
            <CaseLine
              label="Reason codes"
              value={
                <span style={{ display: 'inline-flex', flexWrap: 'wrap', gap: '0.25rem' }}>
                  {(policy.reason_codes ?? []).map((code) => (
                    <span key={code} className="incidentReasonCode">{code}</span>
                  ))}
                </span>
              }
            />
          ) : null}
          {(policy.required_approvals ?? []).length > 0 ? (
            <CaseLine label="Required approvals" value={(policy.required_approvals ?? []).join(', ')} />
          ) : null}
          {policy.evaluated_at ? <CaseLine label="Evaluated" value={formatForensicDate(policy.evaluated_at)} /> : null}
          {/* The verdict is the deterministic engine's, stated as such. An AI
              explanation never occupies this field. */}
          <p className="tableMeta" style={{ margin: '0.3rem 0 0', fontSize: '0.7rem' }}>
            Decided by the deterministic policy engine.
          </p>
        </>
      ) : (
        <p className="muted" style={{ margin: 0, fontSize: '0.82rem' }}>
          No policy evaluation is recorded for this incident.
        </p>
      )}
    </CaseSection>
  );
}

/* ── Where the response stands (Screen 8 owns changing it) ──────────── */
function ResponseSection({ response, responseKnown, responseLoad, incidentId }: {
  response: ReturnType<typeof summarizeResponseState>;
  responseKnown: boolean;
  responseLoad: ForensicLoadState;
  incidentId: string;
}) {
  return (
    <CaseSection
      title="Response"
      pill={
        responseKnown
          ? <StatusPill label={response.label} variant={responseStateVariant(response.state)} />
          : <StatusPill label={responseLoad === 'error' ? 'Unavailable' : 'Loading…'} variant="neutral" />
      }
    >
      {!responseKnown ? (
        <p className="muted" style={{ margin: 0, fontSize: '0.82rem' }}>
          {responseLoad === 'error'
            ? 'Response state could not be read. No response state is shown rather than an unverified one.'
            : 'Reading the response record…'}
        </p>
      ) : response.total > 0 ? (
        <>
          <CaseLine label="Recommended" value={String(response.total)} />
          {response.awaitingApproval > 0 ? <CaseLine label="Awaiting approval" value={String(response.awaitingApproval)} /> : null}
          {response.approved > 0 ? <CaseLine label="Approved" value={String(response.approved)} /> : null}
          {response.executed > 0 ? <CaseLine label="Executed" value={String(response.executed)} /> : null}
          {response.failed > 0 ? <CaseLine label="Failed" value={String(response.failed)} /> : null}
        </>
      ) : (
        <p className="muted" style={{ margin: 0, fontSize: '0.82rem' }}>
          No response action has been recommended for this incident yet.
        </p>
      )}
      {/* Screen 8 owns approval and execution; Screen 7 only reports the state. */}
      <Link
        href={`/response-actions?incident_id=${encodeURIComponent(incidentId)}`}
        prefetch={false}
        className="btn btn-secondary"
        style={{ fontSize: '0.74rem', padding: '0.15rem 0.5rem', marginTop: '0.4rem', alignSelf: 'flex-start' }}
      >
        Open in Response Actions
      </Link>
    </CaseSection>
  );
}

/* ── What proves it ─────────────────────────────────────────────────── */
function EvidenceSection({ evidence }: { evidence: NonNullable<IncidentCaseSummary['evidence']> }) {
  return (
    <CaseSection
      title="Evidence"
      pill={<StatusPill label={snapshotStatusLabel(evidence.snapshot_status)} variant={snapshotStatusVariant(evidence.snapshot_status)} />}
    >
      <CaseLine
        label="Collected"
        value={`${evidence.artifact_count ?? 0} ${evidence.artifact_count === 1 ? 'artifact' : 'artifacts'}`}
      />
      {evidence.snapshot_hash_verified === true ? <CaseLine label="Snapshot hash" value="Verified" /> : null}
      {evidence.snapshot_hash_verified === false ? <CaseLine label="Snapshot hash" value="Mismatch" /> : null}
      {evidence.package_number ? (
        <CaseLine
          label="Evidence package"
          value={
            <Link
              href={evidence.package_route ?? `/evidence?package_id=${encodeURIComponent(evidence.package_id ?? '')}`}
              prefetch={false}
              style={{ fontSize: '0.8rem', color: 'var(--text-accent)' }}
            >
              {evidence.package_number}
            </Link>
          }
        />
      ) : (
        <p className="muted" style={{ margin: 0, fontSize: '0.82rem' }}>
          No evidence package has been created for this incident yet.
        </p>
      )}
    </CaseSection>
  );
}

/* ── Section shell ────────────────────────────────────────────────── */
function CaseSection({ title, state, pill, children }: {
  title: string;
  state?: string;
  pill?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="incidentCaseSection">
      <div className="incidentCaseSectionHead">
        <p className="sectionEyebrow" style={{ margin: 0 }}>{title}</p>
        {pill ?? (state ? <StatusPill label={caseStateLabel(state)} variant={caseStateVariant(state)} /> : null)}
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.25rem' }}>{children}</div>
    </section>
  );
}

/* ── One labelled fact ────────────────────────────────────────────── */
function CaseLine({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="incidentCaseLine">
      <span className="incidentCaseLineLabel">{label}</span>
      <span className="incidentCaseLineValue">{value}</span>
    </div>
  );
}
