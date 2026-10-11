'use client';

import Link from 'next/link';
import { useMemo, useState, type CSSProperties } from 'react';

import { StatusPill, TableShell } from './components/ui-primitives';
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
  policyDecisionVariant,
  shortDigest,
  showsImmutableMark,
  snapshotStatusLabel,
  snapshotStatusVariant,
  type DomainFilter,
  type ForensicLoadState,
  type IncidentEvidenceArtifact,
  type IncidentEvidenceDomain,
  type IncidentEvidencePackage,
  type IncidentForensicSnapshot,
  type IncidentPolicyEvaluation,
} from './incident-forensics-presentation';
import type { IncidentEvidenceResponse } from './use-incident-forensics';

const DIRECTORY_HEADERS = ['File / Artifact', 'Type', 'Domain', 'Source', 'Collected At', 'SHA-256', 'Integrity'];

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
 *
 * Presentational: the payload is fetched once per incident by `useIncidentEvidence`
 * and passed in, so the Case File header, the Overview summary and this directory
 * all describe the same evidence record — and the page issues one request for it,
 * not one per consumer.
 */
export default function IncidentEvidenceTab({ data, load, onRetry }: {
  data: IncidentEvidenceResponse | null;
  load: ForensicLoadState;
  onRetry: () => void;
}) {
  const [filter, setFilter] = useState<DomainFilter>('ALL');

  const artifacts = useMemo(() => data?.artifacts ?? [], [data]);
  const visible = useMemo(() => filterArtifacts(artifacts, filter), [artifacts, filter]);
  const evaluations = useMemo(
    () => enforcementEvaluations(data?.policy_evaluations),
    [data],
  );

  if (load === 'idle' || load === 'loading') {
    return <p className="muted incidentEvidenceState" aria-busy="true">Loading incident evidence…</p>;
  }
  if (load === 'unauthorized') {
    return (
      <p className="muted incidentEvidenceState" role="alert">
        You do not have permission to view this incident&apos;s evidence in the current workspace.
      </p>
    );
  }
  if (load === 'not_found') {
    return (
      <p className="muted incidentEvidenceState" role="alert">
        This incident could not be found in the current workspace.
      </p>
    );
  }
  if (load === 'error') {
    return (
      <div role="alert" className="incidentEvidenceError">
        <p className="muted incidentEvidenceState">
          Evidence is unavailable — the evidence service could not be reached. No evidence is shown rather than a partial view presented as complete.
        </p>
        <button type="button" className="btn btn-secondary incidentEvidenceAction" onClick={onRetry}>
          Retry
        </button>
      </div>
    );
  }

  const counts = data?.counts ?? {};
  const snapshot = data?.snapshot ?? {};
  const evidencePackage = data?.evidence_package ?? {};

  return (
    <div className="incidentEvidenceTab">
      {/* A read that FAILED is stated. A partial directory is never presented as
          the complete evidence record. */}
      {data?.partial ? (
        <p className="statusLine statusLine-warning incidentEvidenceNotice" role="alert">
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
        <p className="muted incidentEvidenceEmpty">{emptyEvidenceMessage(filter)}</p>
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
        <p className="muted incidentEvidenceCaption">
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
        '--incident-domain-accent': accent,
        '--incident-domain-surface': surface,
        '--incident-domain-border': border,
      } as CSSProperties}
    >
      <span className="incidentEvidenceDomainLabel">{label}</span>
      {/* A count the backend did not report is "not reported", never a rendered 0
          — an absent bucket must not read as "there is none". */}
      <span className="incidentEvidenceDomainCount">
        {count === null ? '—' : count}
      </span>
      <span className="incidentEvidenceDomainUnit">
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
      <div className="incidentEvidenceSnapshotGroup">
        <StatusPill
          label={snapshotStatusLabel(snapshot.status)}
          variant={snapshotStatusVariant(snapshot.status)}
        />
        {/* The snapshot digest is shown only when one is persisted, and the
            verification result is reported as the backend computed it — a hash that
            did not re-compute is stated, never hidden. */}
        {snapshot.snapshot_hash ? (
          <span className="incidentEvidenceDigest" title={snapshot.snapshot_hash}>
            {shortDigest(snapshot.snapshot_hash)}
          </span>
        ) : (
          <span className="muted incidentEvidenceMeta">No evidence snapshot recorded yet</span>
        )}
        {snapshot.hash_verified === true ? <StatusPill label="Hash verified" variant="success" /> : null}
        {snapshot.hash_verified === false ? <StatusPill label="Hash mismatch" variant="danger" /> : null}
        {snapshot.hash_verified === null || snapshot.hash_verified === undefined
          ? <StatusPill label="Hash not verified" variant="neutral" />
          : null}
      </div>
      <div className="incidentEvidenceSnapshotGroup">
        {packaged ? (
          <>
            <span className="incidentEvidencePackageNo">{evidencePackage.package_number}</span>
            <StatusPill
              label={evidencePackage.integrity_label ?? 'Unknown'}
              variant={evidencePackage.integrity_status === 'verified' ? 'success' : 'info'}
            />
            {evidencePackage.sealed_at ? (
              <span className="tableMeta incidentEvidenceMeta">
                Sealed {formatForensicDate(evidencePackage.sealed_at)}
              </span>
            ) : null}
            {/* Screen 9 owns packaging, verification and download. Screen 7 links
                to it and never re-implements any of them. */}
            <Link
              href={evidencePackage.route ?? `/evidence?package_id=${encodeURIComponent(evidencePackage.package_id ?? '')}`}
              prefetch={false}
              className="btn btn-secondary incidentEvidenceAction"
            >
              View Evidence Package
            </Link>
          </>
        ) : (
          <span className="muted incidentEvidenceMeta">
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
      <p className="sectionEyebrow incidentPolicyEyebrow">Policy forensics</p>
      <div className="incidentPolicyStack">
        {evaluations.map((evaluation) => (
          <div key={evaluation.evaluation_id} className="incidentPolicyRow">
            <div className="incidentPolicyHead">
              <span className="incidentPolicyKey">
                {evaluation.policy_key ?? 'Policy'}
              </span>
              {evaluation.policy_version !== null && evaluation.policy_version !== undefined ? (
                <span className="tableMeta incidentPolicyMeta">v{evaluation.policy_version}</span>
              ) : null}
              {/* The authoritative decision, verbatim from the deterministic engine.
                  No AI explanation is ever promoted into this field. */}
              <StatusPill label={evaluation.decision ?? 'No decision recorded'} variant={policyDecisionVariant(evaluation.decision)} />
              <span className="tableMeta incidentPolicyMeta">
                {formatForensicDate(evaluation.evaluated_at)}
              </span>
            </div>
            {(evaluation.reason_codes ?? []).length > 0 ? (
              <div className="incidentReasonCodeList incidentPolicyCodes">
                {(evaluation.reason_codes ?? []).map((code) => (
                  <span key={code} className="incidentReasonCode">{code}</span>
                ))}
              </div>
            ) : null}
            {(evaluation.required_approvals ?? []).length > 0 ? (
              <p className="tableMeta incidentPolicyApprovals">
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
      <td className="incidentArtifactName" title={artifact.file_name ?? undefined}>
        {artifact.file_name ?? 'Unnamed artifact'}
      </td>
      <td className="incidentArtifactType">{artifactTypeLabel(artifact.artifact_type)}</td>
      <td>
        {domain ? (
          <span className="incidentDomainTag incidentArtifactDomain"
            style={{ '--incident-domain-accent': domainAccentVar(domain), '--incident-domain-border': domainBorderVar(domain) } as CSSProperties}>
            {domainLabel(domain)}
          </span>
        ) : (
          <span className="muted incidentArtifactMuted">Unclassified</span>
        )}
      </td>
      <td className="incidentArtifactSource">
        <span className="incidentArtifactInline">
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
      <td className="incidentArtifactTime" title={artifact.collected_at ?? undefined}>
        {/* Millisecond time at the precision the record carries, with the calendar
            day beneath it: a directory can span days, and a bare time would make
            records from different days look like one collection burst. */}
        {artifact.collected_at ? (
          <>
            <span className="incidentArtifactTimeValue">{formatForensicTime(artifact.collected_at)}</span>
            <span className="tableMeta incidentArtifactDay">
              {forensicDay(artifact.collected_at) ?? ''}
            </span>
          </>
        ) : (
          <span className="muted">Not recorded</span>
        )}
      </td>
      <td className="incidentArtifactDigest" title={artifact.content_sha256 ?? undefined}>
        {/* No digest means no digest. A placeholder hash is never rendered. */}
        {digest ?? <span className="muted">Not hashed</span>}
      </td>
      <td>
        <span className="incidentArtifactInline">
          <StatusPill
            label={artifact.integrity_label ?? integrityLabel(artifact.integrity_status)}
            variant={integrityVariant(artifact.integrity_status)}
          />
          {/* The tamper-evident mark is reserved for an artifact the backend sealed
              inside a hash-verified snapshot. An ordinary row never earns it. */}
          {showsImmutableMark(artifact) ? (
            <span aria-label="Tamper-evident" title="Sealed inside a hash-verified evidence snapshot"
              className="incidentArtifactSealMark">✓</span>
          ) : null}
        </span>
      </td>
    </tr>
  );
}
