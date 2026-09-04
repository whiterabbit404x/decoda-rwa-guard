'use client';

import Link from 'next/link';
import { useCallback, useEffect, useMemo, useState } from 'react';

import { StatusPill, TableShell } from './components/ui-primitives';
import { usePilotAuth } from './pilot-auth-context';
import {
  EVIDENCE_DOMAINS,
  artifactEvidenceSource,
  artifactTypeLabel,
  domainAccentVar,
  domainBorderVar,
  domainCount,
  domainLabel,
  domainSurfaceVar,
  emptyEvidenceMessage,
  enforcementEvaluations,
  evidencePackageAbsenceLabel,
  filterArtifacts,
  forensicDay,
  formatForensicDate,
  formatForensicTime,
  hasEvidencePackage,
  integrityLabel,
  integrityVariant,
  linkScopeCaveat,
  loadStateFor,
  policyDecisionVariant,
  shortDigest,
  showsImmutableMark,
  snapshotStatusLabel,
  snapshotStatusVariant,
  type DomainFilter,
  type ForensicLoadState,
  type IncidentEvidenceArtifact,
  type IncidentEvidenceCounts,
  type IncidentEvidenceDomain,
  type IncidentEvidencePackage,
  type IncidentForensicSnapshot,
  type IncidentPolicyEvaluation,
} from './incident-forensics-presentation';

// Same-origin proxy base — the Incidents UI never calls the backend directly (the
// browser only sees NEXT_PUBLIC_API_URL, often unset in production). Every call
// goes through the Next.js /api/* proxy, the same transport the rest of Screen 7 uses.
const API_PROXY_BASE = '/api';

const DIRECTORY_HEADERS = ['File / Artifact', 'Type', 'Domain', 'Source', 'Collected At', 'SHA-256', 'Integrity'];

type EvidenceResponse = {
  incident_id?: string;
  event_id?: string | null;
  counts?: IncidentEvidenceCounts;
  snapshot?: IncidentForensicSnapshot;
  evidence_package?: IncidentEvidencePackage;
  policy_evaluations?: IncidentPolicyEvaluation[];
  artifacts?: IncidentEvidenceArtifact[];
  truncated?: boolean;
  partial?: boolean;
  unreadable?: string[];
};

/**
 * Screen 7 — Evidence tab (forensic directory).
 *
 * The incident's collected artifacts, split into the four provenance domains the
 * backend classified them into: what the CHAIN recorded, what the OPERATIONAL
 * systems of record said, what the deterministic POLICY engine decided, and what
 * PEOPLE did. Those four axes are exactly where "cryptographically valid" and
 * "operationally authorized" come apart, which is the case this screen has to make
 * auditable.
 *
 * Everything rendered here is a real backend record. Counts come from the API (never
 * from a hard-coded number), hashes are the digests the backend computed (never a
 * hashed display string), and "sealed"/"immutable" appear only where the backend
 * asserted them. A domain with no records says so.
 */
export default function IncidentEvidenceTab({ incidentId }: { incidentId: string }) {
  const { authHeaders } = usePilotAuth();
  const [data, setData] = useState<EvidenceResponse | null>(null);
  const [load, setLoad] = useState<ForensicLoadState>('idle');
  const [filter, setFilter] = useState<DomainFilter>('ALL');

  const fetchEvidence = useCallback(async (): Promise<void> => {
    setLoad('loading');
    try {
      const res = await fetch(`${API_PROXY_BASE}/incidents/${encodeURIComponent(incidentId)}/evidence`, {
        headers: authHeaders(),
        cache: 'no-store',
      });
      if (!res.ok) {
        setData(null);
        setLoad(loadStateFor(res.status, false));
        return;
      }
      const json = (await res.json()) as EvidenceResponse;
      setData(json);
      setLoad(loadStateFor(res.status, (json.artifacts ?? []).length > 0));
    } catch {
      setData(null);
      setLoad('error');
    }
  }, [authHeaders, incidentId]);

  useEffect(() => {
    if (!incidentId) { setData(null); setLoad('idle'); return; }
    void fetchEvidence();
  }, [fetchEvidence, incidentId]);

  const artifacts = useMemo(() => data?.artifacts ?? [], [data]);
  const visible = useMemo(() => filterArtifacts(artifacts, filter), [artifacts, filter]);
  const evaluations = useMemo(
    () => enforcementEvaluations(data?.policy_evaluations),
    [data],
  );

  if (load === 'idle' || load === 'loading') {
    return <p className="muted" style={{ fontSize: '0.85rem' }} aria-busy="true">Loading incident evidence…</p>;
  }
  if (load === 'unauthorized') {
    return (
      <p className="muted" style={{ fontSize: '0.85rem' }} role="alert">
        You do not have permission to view this incident&apos;s evidence in the current workspace.
      </p>
    );
  }
  if (load === 'not_found') {
    return (
      <p className="muted" style={{ fontSize: '0.85rem' }} role="alert">
        This incident could not be found in the current workspace.
      </p>
    );
  }
  if (load === 'error') {
    return (
      <div role="alert">
        <p className="muted" style={{ fontSize: '0.85rem', marginBottom: '0.5rem' }}>
          Evidence is unavailable — the evidence service could not be reached. No evidence is shown rather than a partial view presented as complete.
        </p>
        <button type="button" className="btn btn-secondary" style={{ fontSize: '0.78rem' }} onClick={() => void fetchEvidence()}>
          Retry
        </button>
      </div>
    );
  }

  const counts = data?.counts ?? {};
  const snapshot = data?.snapshot ?? {};
  const evidencePackage = data?.evidence_package ?? {};

  return (
    <div className="incidentEvidenceTab" style={{ display: 'flex', flexDirection: 'column', gap: '0.85rem' }}>
      {/* A read that FAILED is stated. A partial directory is never presented as
          the complete evidence record. */}
      {data?.partial ? (
        <p className="statusLine statusLine-warning" role="alert" style={{ margin: 0, fontSize: '0.8rem' }}>
          Partial evidence: {(data.unreadable ?? []).join(', ') || 'one or more sources'} could not be read. Records from
          those sources are missing from this directory.
        </p>
      ) : null}

      <SnapshotStrip snapshot={snapshot} evidencePackage={evidencePackage} />

      {/* ── Four forensic domain cards. Counts come from the backend; clicking a
             card filters the directory below. ─────────────────────────────── */}
      <div className="incidentEvidenceDomains" role="group" aria-label="Evidence domains">
        <DomainCard
          label="All"
          count={typeof counts.total === 'number' ? counts.total : null}
          active={filter === 'ALL'}
          accent="var(--text-accent)"
          surface="var(--accent-blue-bg)"
          border="var(--border-accent)"
          onSelect={() => setFilter('ALL')}
        />
        {EVIDENCE_DOMAINS.map((domain) => (
          <DomainCard
            key={domain}
            label={domainLabel(domain)}
            count={domainCount(counts, domain)}
            active={filter === domain}
            accent={domainAccentVar(domain)}
            surface={domainSurfaceVar(domain)}
            border={domainBorderVar(domain)}
            onSelect={() => setFilter(domain)}
          />
        ))}
      </div>

      {evaluations.length > 0 ? <PolicyForensics evaluations={evaluations} /> : null}

      {/* ── Forensic evidence directory ──────────────────────────────────── */}
      {visible.length === 0 ? (
        <p className="muted" style={{ fontSize: '0.85rem', margin: 0 }}>{emptyEvidenceMessage(filter)}</p>
      ) : (
        <div className="incidentEvidenceTable">
          <TableShell headers={DIRECTORY_HEADERS} compact>
            {visible.map((artifact) => (
              <ArtifactRow key={artifact.id} artifact={artifact} />
            ))}
          </TableShell>
        </div>
      )}

      {data?.truncated ? (
        <p className="muted" style={{ fontSize: '0.78rem', margin: 0 }}>
          This directory is capped. The complete inventory remains available through the evidence package.
        </p>
      ) : null}
    </div>
  );
}

/* ── Domain summary card ──────────────────────────────────────────── */
function DomainCard({ label, count, active, accent, surface, border, onSelect }: {
  label: string;
  count: number | null;
  active: boolean;
  accent: string;
  surface: string;
  border: string;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={active}
      className="incidentEvidenceDomainCard"
      style={{
        background: active ? surface : 'rgba(148,163,184,0.05)',
        border: `1px solid ${active ? border : 'var(--border)'}`,
        borderLeft: `3px solid ${accent}`,
      }}
    >
      <span className="tableMeta" style={{ fontSize: '0.72rem' }}>{label}</span>
      {/* A count the backend did not report is "not reported", never a rendered 0
          — an absent bucket must not read as "there is none". */}
      <span style={{ fontSize: '1.15rem', fontWeight: 700, color: accent, lineHeight: 1.1 }}>
        {count === null ? '—' : count}
      </span>
      <span className="tableMeta" style={{ fontSize: '0.68rem' }}>
        {count === null ? 'not reported' : count === 1 ? 'artifact' : 'artifacts'}
      </span>
    </button>
  );
}

/* ── Snapshot + Screen 9 package state ────────────────────────────── */
function SnapshotStrip({ snapshot, evidencePackage }: {
  snapshot: IncidentForensicSnapshot;
  evidencePackage: IncidentEvidencePackage;
}) {
  const packaged = hasEvidencePackage(evidencePackage);
  return (
    <div className="incidentEvidenceSnapshot">
      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '0.5rem' }}>
        <StatusPill
          label={snapshotStatusLabel(snapshot.status)}
          variant={snapshotStatusVariant(snapshot.status)}
        />
        {/* The snapshot digest is shown only when one is persisted, and the
            verification result is reported as the backend computed it — a hash that
            did not re-compute is stated, never hidden. */}
        {snapshot.snapshot_hash ? (
          <span style={{ fontFamily: 'monospace', fontSize: '0.72rem', color: 'var(--text-secondary)' }}
            title={snapshot.snapshot_hash}>
            {shortDigest(snapshot.snapshot_hash)}
          </span>
        ) : (
          <span className="muted" style={{ fontSize: '0.75rem' }}>No evidence snapshot recorded yet</span>
        )}
        {snapshot.hash_verified === true ? <StatusPill label="Hash verified" variant="success" /> : null}
        {snapshot.hash_verified === false ? <StatusPill label="Hash mismatch" variant="danger" /> : null}
        {snapshot.hash_verified === null || snapshot.hash_verified === undefined
          ? <StatusPill label="Hash not verified" variant="neutral" />
          : null}
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '0.5rem' }}>
        {packaged ? (
          <>
            <span style={{ fontFamily: 'monospace', fontSize: '0.75rem' }}>{evidencePackage.package_number}</span>
            <StatusPill
              label={evidencePackage.integrity_label ?? 'Unknown'}
              variant={evidencePackage.integrity_status === 'verified' ? 'success' : 'info'}
            />
            {evidencePackage.sealed_at ? (
              <span className="tableMeta" style={{ fontSize: '0.72rem' }}>
                Sealed {formatForensicDate(evidencePackage.sealed_at)}
              </span>
            ) : null}
            {/* Screen 9 owns packaging, verification and download. Screen 7 links
                to it and never re-implements any of them. */}
            <Link
              href={evidencePackage.route ?? `/evidence?package_id=${encodeURIComponent(evidencePackage.package_id ?? '')}`}
              prefetch={false}
              className="btn btn-secondary"
              style={{ fontSize: '0.75rem', padding: '0.15rem 0.5rem' }}
            >
              View Evidence Package
            </Link>
          </>
        ) : (
          <span className="muted" style={{ fontSize: '0.78rem' }}>
            {evidencePackageAbsenceLabel(evidencePackage)}
          </span>
        )}
      </div>
    </div>
  );
}

/* ── Policy forensics (Screen 11's deterministic verdict) ─────────── */
function PolicyForensics({ evaluations }: { evaluations: IncidentPolicyEvaluation[] }) {
  return (
    <div className="incidentPolicyForensics" aria-label="Policy forensics">
      <p className="sectionEyebrow" style={{ margin: '0 0 0.4rem' }}>Policy forensics</p>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
        {evaluations.map((evaluation) => (
          <div key={evaluation.evaluation_id} className="incidentPolicyRow">
            <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '0.45rem' }}>
              <span style={{ fontFamily: 'monospace', fontSize: '0.78rem' }}>
                {evaluation.policy_key ?? 'Policy'}
              </span>
              {evaluation.policy_version !== null && evaluation.policy_version !== undefined ? (
                <span className="tableMeta" style={{ fontSize: '0.72rem' }}>v{evaluation.policy_version}</span>
              ) : null}
              {/* The authoritative decision, verbatim from the deterministic engine.
                  No AI explanation is ever promoted into this field. */}
              <StatusPill label={evaluation.decision ?? 'No decision recorded'} variant={policyDecisionVariant(evaluation.decision)} />
              <span className="tableMeta" style={{ fontSize: '0.72rem' }}>
                {formatForensicDate(evaluation.evaluated_at)}
              </span>
            </div>
            {(evaluation.reason_codes ?? []).length > 0 ? (
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.25rem', marginTop: '0.3rem' }}>
                {(evaluation.reason_codes ?? []).map((code) => (
                  <span key={code} className="incidentReasonCode">{code}</span>
                ))}
              </div>
            ) : null}
            {(evaluation.required_approvals ?? []).length > 0 ? (
              <p className="tableMeta" style={{ margin: '0.3rem 0 0', fontSize: '0.72rem' }}>
                Required approval roles: {(evaluation.required_approvals ?? []).join(', ')}
              </p>
            ) : null}
          </div>
        ))}
      </div>
    </div>
  );
}

/* ── One directory row ────────────────────────────────────────────── */
function ArtifactRow({ artifact }: { artifact: IncidentEvidenceArtifact }) {
  const digest = shortDigest(artifact.content_sha256);
  const domain = (artifact.domain ?? null) as IncidentEvidenceDomain | null;
  const caveat = linkScopeCaveat(artifact);
  const provenance = artifactEvidenceSource(artifact);
  return (
    <tr>
      <td style={{ fontFamily: 'monospace', fontSize: '0.74rem' }} title={artifact.file_name ?? undefined}>
        {artifact.file_name ?? 'Unnamed artifact'}
      </td>
      <td style={{ fontSize: '0.78rem' }}>{artifactTypeLabel(artifact.artifact_type)}</td>
      <td>
        {domain ? (
          <span className="incidentDomainTag" style={{ color: domainAccentVar(domain), borderColor: domainBorderVar(domain) }}>
            {domainLabel(domain)}
          </span>
        ) : (
          <span className="muted" style={{ fontSize: '0.74rem' }}>Unclassified</span>
        )}
      </td>
      <td style={{ fontSize: '0.76rem', color: 'var(--text-secondary)' }}>
        <span style={{ display: 'inline-flex', flexWrap: 'wrap', alignItems: 'center', gap: '0.3rem' }}>
          <span>{artifact.source ?? 'Unknown source'}</span>
          {/* Evidence provenance, in the product's existing vocabulary: simulator
              and replay data are labelled as such so they can never be read as
              live customer evidence. */}
          {provenance ? <StatusPill label={provenance.label} variant={provenance.variant} /> : null}
          {/* A record linked only by the ASSET is not this event's record. The
              weaker link is stated on the row so it can never read as event
              evidence. Event- and incident-linked rows carry no caveat. */}
          {caveat ? (
            <span className="incidentLinkScope" title="Linked to the asset, not to this incident's canonical event">
              {caveat}
            </span>
          ) : null}
        </span>
      </td>
      <td style={{ fontSize: '0.74rem', whiteSpace: 'nowrap' }} title={artifact.collected_at ?? undefined}>
        {/* Millisecond time at the precision the record carries, with the calendar
            day beneath it: a directory can span days, and a bare time would make
            records from different days look like one collection burst. */}
        {artifact.collected_at ? (
          <>
            <span style={{ fontFamily: 'monospace' }}>{formatForensicTime(artifact.collected_at)}</span>
            <span className="tableMeta" style={{ display: 'block', fontSize: '0.68rem' }}>
              {forensicDay(artifact.collected_at) ?? ''}
            </span>
          </>
        ) : (
          <span className="muted">Not recorded</span>
        )}
      </td>
      <td style={{ fontFamily: 'monospace', fontSize: '0.72rem' }} title={artifact.content_sha256 ?? undefined}>
        {/* No digest means no digest. A placeholder hash is never rendered. */}
        {digest ?? <span className="muted">Not hashed</span>}
      </td>
      <td>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: '0.3rem' }}>
          <StatusPill
            label={artifact.integrity_label ?? integrityLabel(artifact.integrity_status)}
            variant={integrityVariant(artifact.integrity_status)}
          />
          {/* The tamper-evident mark is reserved for an artifact the backend sealed
              inside a hash-verified snapshot. An ordinary row never earns it. */}
          {showsImmutableMark(artifact) ? (
            <span aria-label="Tamper-evident" title="Sealed inside a hash-verified evidence snapshot"
              style={{ color: 'var(--success-fg)', fontSize: '0.8rem' }}>✓</span>
          ) : null}
        </span>
      </td>
    </tr>
  );
}
