'use client';

/**
 * Screen 9 — Verifiable Evidence Package presentation components.
 *
 * The rule every component here obeys: nothing on this surface computes a
 * verification outcome. Each renders EXACTLY what the backend verification
 * service concluded, category by category. There is no frontend-only verified
 * state, no optimistic default and no hash, root or signer value invented here
 * for display.
 *
 * In particular:
 *   * the green VERIFIED shield renders only when the backend returned
 *     status === 'VERIFIED' — every other status, including a merely generated
 *     or merely signed package, renders as a non-green state;
 *   * a check whose backend status is `unavailable` shows as "could not be
 *     checked", never as a pass and never as a failure;
 *   * a package that has never been verified shows an explicit "not verified
 *     yet" state rather than any success affordance;
 *   * "HSM/KMS-backed" is printed only when the backend's signer metadata sets
 *     hardware_backed === true. The signer abstraction existing is not a claim
 *     that hardware custody does.
 */

import { StatusPill, type PillVariant } from './components/ui-primitives';

/* ── Types (mirror the backend verification contract) ─────────────── */

export type VerificationCheck = {
  check: string;
  status: 'passed' | 'failed' | 'unavailable' | 'not_applicable' | string;
  label: string;
  detail?: string | null;
  mandatory?: boolean;
  passed?: boolean;
  valid?: number;
  total?: number;
  failed_artifact_paths?: string[];
  missing_artifact_paths?: string[];
  expected?: string | null;
  computed?: string | null;
  key_id?: string | null;
  provider?: string | null;
  policy_key?: string | null;
  policy_version?: number | string | null;
};

export type SignerMetadata = {
  provider?: string | null;
  algorithm?: string | null;
  key_id?: string | null;
  key_version?: string | null;
  hardware_backed?: boolean;
  assurance?: string | null;
  assurance_label?: string | null;
  key_custody?: string | null;
  production_grade?: boolean;
  warning?: string | null;
  signed?: boolean;
  signed_at?: string | null;
};

export type VerificationResult = {
  status: string;
  status_label?: string;
  verified?: boolean;
  verified_at?: string | null;
  schema_version?: string;
  checks?: VerificationCheck[];
  failed_checks?: string[];
  unavailable_checks?: string[];
  artifact_hashes?: { valid?: number; total?: number; failed_artifact_ids?: string[]; missing_artifact_ids?: string[] };
  merkle_root?: { valid?: boolean; status?: string; expected?: string | null; computed?: string | null };
  manifest_signature?: { valid?: boolean; state?: string | null; key_id?: string | null; provider?: string | null };
  signer?: SignerMetadata;
};

/**
 * THE canonical verification contract (backend ``build_verification_contract``).
 *
 * One backend result. The package table's Integrity column, the package detail
 * view, the Crypto-Auditing Clerk and the Verification Checklist all render THIS
 * — none of them computes a verification outcome of its own. That is the fix for
 * the contradiction production showed, where the checklist was served from a
 * build-time completeness snapshot frozen before verification could have run and
 * therefore said "Hashes verified ✗" beside Files Verified = 9.
 */
export type VerificationContract = {
  overall_status: string;
  overall_label?: string;
  verified?: boolean;
  /** A real server-side verification has run. False => nothing to render as an outcome. */
  executed?: boolean;
  verified_at?: string | null;
  integrity_status?: string | null;
  shield?: { state: string; label: string; verified?: boolean };
  artifact_hashes?: {
    /** Artifacts carrying a stored SHA-256 (a packaging fact). */
    files_hashed?: number;
    /** Artifacts whose current bytes were recomputed AND matched. */
    files_verified?: number;
    verifiable_count?: number;
    total?: number;
    hash_failures?: number;
    failed_artifact_ids?: string[];
    missing_artifact_ids?: string[];
    hashes_verified?: boolean;
    status?: string;
    label?: string;
  };
  merkle_root?: VerificationCategory;
  manifest_signature?: VerificationCategory;
  policy_snapshot?: VerificationCategory;
  provenance?: VerificationCategory;
  required_evidence?: VerificationCategory;
  manifest_hash?: VerificationCategory;
  /** Evidence completeness — reported alongside, never an input to the status. */
  completeness?: {
    score?: number | null;
    status?: string | null;
    required_count?: number | null;
    present_count?: number | null;
    missing_count?: number | null;
    unverifiable_count?: number | null;
    complete?: boolean;
  };
  checklist?: VerificationChecklistItem[];
  checks?: VerificationCheck[];
  signer?: SignerMetadata | null;
  failed_checks?: string[];
  unavailable_checks?: string[];
};

export type VerificationCategory = {
  status: string;
  valid?: boolean;
  label?: string;
  detail?: string | null;
  expected?: string | null;
  computed?: string | null;
  state?: string | null;
  key_id?: string | null;
  provider?: string | null;
  algorithm?: string | null;
};

export type VerificationChecklistItem = {
  code: string;
  label: string;
  /** passed | failed | not_verified | unavailable | not_applicable */
  state: string;
  present?: boolean;
  /** verification | completeness | packaging — what kind of fact this row is. */
  source?: string;
  detail?: string | null;
};

export type PolicySnapshot = {
  present?: boolean;
  reason?: string | null;
  policy_key?: string | null;
  policy_version?: number | null;
  decision?: string | null;
  decision_kind?: string | null;
  evaluated_at?: string | null;
  source?: string | null;
};

export type PackageContentEntry = {
  key: string;
  label: string;
  kind: string;
  path: string;
  count?: number;
  declared_count?: number;
  available: boolean;
  unavailable_reason?: string | null;
  items?: string[];
  media_type?: string;
};

/** Client-side verification phase. VERIFYING is the only state the UI owns. */
export type VerifyPhase = 'idle' | 'verifying';

/* ── Status vocabulary ────────────────────────────────────────────── */

// The ONE status that may render green. Everything else is amber, red or
// neutral — a package that generated fine but was never verified is not green.
export const VERIFIED_STATUS = 'VERIFIED';

const STATUS_PRESENTATION: Record<string, { label: string; variant: PillVariant; tone: string }> = {
  VERIFIED: { label: 'Verified', variant: 'success', tone: '#22c55e' },
  PARTIALLY_VERIFIED: { label: 'Partially Verified', variant: 'warning', tone: '#f59e0b' },
  VERIFICATION_FAILED: { label: 'Verification Failed', variant: 'danger', tone: '#ef4444' },
  SIGNATURE_UNAVAILABLE: { label: 'Signature Unavailable', variant: 'warning', tone: '#f59e0b' },
  INCOMPLETE_PACKAGE: { label: 'Incomplete Package', variant: 'warning', tone: '#f59e0b' },
  VERIFYING: { label: 'Verifying…', variant: 'info', tone: '#60a5fa' },
};

const NOT_VERIFIED = { label: 'Not Verified', variant: 'neutral' as PillVariant, tone: '#94a3b8' };

// Canonical shield states (backend ``_shield_for``). The green shield is licensed
// by exactly ONE of them — never by an evidence-completeness percentage.
export const SHIELD_PRESENTATION: Record<string, { label: string; tone: string; body: string }> = {
  VERIFIED: {
    label: 'Verified',
    tone: '#22c55e',
    body: 'This package is cryptographically sealed and tamper-evident: every artifact hash, the Merkle root and the manifest signature were recomputed on the server and matched.',
  },
  READY_FOR_VERIFICATION: {
    label: 'Ready for Verification',
    tone: '#60a5fa',
    body: 'All required evidence is present and hashed, but integrity has not been verified yet. Run Verify Integrity to check this package against its stored bytes.',
  },
  INTEGRITY_CHECK_FAILED: {
    label: 'Integrity Check Failed',
    tone: '#ef4444',
    body: 'One or more cryptographic checks FAILED. This package must not be presented as proof.',
  },
  NOT_FULLY_VERIFIED: {
    label: 'Not Fully Verified',
    tone: '#f59e0b',
    body: 'Everything checkable passed, but at least one check could not be run. This package is not fully verified — that is not evidence of tampering.',
  },
  INCOMPLETE_PACKAGE: {
    label: 'Incomplete Package',
    tone: '#f59e0b',
    body: 'Evidence this package declares is missing, so it cannot be verified in full.',
  },
  NOT_VERIFIABLE: {
    label: 'Not Verifiable',
    tone: '#94a3b8',
    body: 'This package has no retrievable signed manifest, so its integrity cannot be verified.',
  },
  SUPERSEDED: {
    label: 'Superseded',
    tone: '#94a3b8',
    body: 'A newer package supersedes this one. Its historical state is preserved as-is.',
  },
  BUILDING: {
    label: 'Building',
    tone: '#f59e0b',
    body: 'This package is still being generated. There is nothing to verify yet.',
  },
};

export function verificationStatusPresentation(status?: string | null) {
  if (!status) return NOT_VERIFIED;
  return STATUS_PRESENTATION[status] ?? NOT_VERIFIED;
}

const CHECK_MARKS: Record<string, { glyph: string; color: string; srLabel: string }> = {
  passed: { glyph: '✓', color: '#22c55e', srLabel: 'passed' },
  failed: { glyph: '✕', color: '#ef4444', srLabel: 'failed' },
  unavailable: { glyph: '?', color: '#f59e0b', srLabel: 'could not be checked' },
  // A check that has never been RUN is not a failure. Rendering it as a red ✗ is
  // exactly as untruthful as rendering it as a green ✓ — it gets its own neutral
  // "○ Not verified" mark.
  not_verified: { glyph: '○', color: '#94a3b8', srLabel: 'not verified yet' },
  not_applicable: { glyph: '–', color: '#94a3b8', srLabel: 'not applicable to this package' },
};

function checkMark(status: string) {
  return CHECK_MARKS[status] ?? CHECK_MARKS.not_applicable;
}

/* ── Shared bits ──────────────────────────────────────────────────── */

export function truncateHash(value?: string | null, head = 10, tail = 6): string {
  const hash = String(value ?? '').trim();
  if (!hash) return '—';
  if (hash.length <= head + tail + 1) return hash;
  return `${hash.slice(0, head)}…${hash.slice(-tail)}`;
}

function MetaRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'baseline',
        justifyContent: 'space-between',
        gap: '0.75rem',
        padding: '0.3rem 0',
        borderBottom: '1px solid rgba(148,163,184,0.08)',
      }}
    >
      <span className="tableMeta" style={{ flex: '0 0 auto' }}>{label}</span>
      <span style={{ fontSize: '0.8rem', color: '#e2e8f0', textAlign: 'right', wordBreak: 'break-all' }}>
        {children}
      </span>
    </div>
  );
}

/* ── 1. Package summary (cryptographic identity) ──────────────────── */

export function PackageCryptoSummary({
  packageNumber,
  incidentLabel,
  createdAt,
  artifactCount,
  merkleRoot,
  manifestSha256,
  hashAlgorithm,
  merkleScheme,
  manifestSchemaVersion,
  signing,
  policySnapshot,
  verificationStatus,
  lastVerifiedAt,
}: {
  packageNumber: string;
  incidentLabel?: string | null;
  createdAt?: string | null;
  artifactCount?: number | null;
  merkleRoot?: string | null;
  manifestSha256?: string | null;
  hashAlgorithm?: string | null;
  merkleScheme?: string | null;
  manifestSchemaVersion?: string | null;
  signing?: SignerMetadata | null;
  policySnapshot?: PolicySnapshot | null;
  verificationStatus?: string | null;
  /** From the canonical contract. Null means never verified — never "unknown". */
  lastVerifiedAt?: string | null;
}) {
  const presentation = verificationStatusPresentation(verificationStatus);
  const signed = Boolean(signing?.signed);
  // The signature row reports SIGNING, not verification. "Signed" and "verified"
  // are different facts and are never merged into one word here.
  const signatureLabel = signed ? 'Signed' : 'Not signed';

  return (
    <section aria-label="Evidence package" style={{ marginBottom: '0.9rem' }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: '0.75rem',
          marginBottom: '0.5rem',
        }}
      >
        <div>
          <p className="sectionEyebrow" style={{ margin: 0 }}>Evidence Package</p>
          <h3 style={{ margin: '0.1rem 0 0', fontSize: '1.05rem', letterSpacing: '0.01em' }}>{packageNumber}</h3>
        </div>
        <StatusPill label={presentation.label} variant={presentation.variant} />
      </div>

      <div>
        <MetaRow label="Incident">{incidentLabel || '—'}</MetaRow>
        <MetaRow label="Created">
          {createdAt ? new Date(createdAt).toLocaleString() : '—'}
        </MetaRow>
        <MetaRow label="Artifacts">{typeof artifactCount === 'number' ? artifactCount : '—'}</MetaRow>
        <MetaRow label="Package Version">
          {manifestSchemaVersion ? `Manifest schema ${manifestSchemaVersion}` : '—'}
        </MetaRow>
        <MetaRow label="Merkle Root">
          {merkleRoot ? (
            <code title={merkleRoot} style={{ fontSize: '0.76rem' }}>{truncateHash(merkleRoot)}</code>
          ) : (
            // Never a placeholder hash: a package sealed before the Merkle
            // commitment existed says so plainly.
            <span style={{ color: '#94a3b8' }}>
              Not sealed in this package
              {manifestSchemaVersion ? ` (manifest ${manifestSchemaVersion})` : ''}
            </span>
          )}
        </MetaRow>
        <MetaRow label="Manifest Hash">
          {manifestSha256 ? (
            <code title={manifestSha256} style={{ fontSize: '0.76rem' }}>{truncateHash(manifestSha256)}</code>
          ) : (
            <span style={{ color: '#94a3b8' }}>No retrievable manifest</span>
          )}
        </MetaRow>
        <MetaRow label="Hash Algorithm">{hashAlgorithm || '—'}</MetaRow>
        {merkleScheme ? <MetaRow label="Merkle Scheme">{merkleScheme}</MetaRow> : null}
        <MetaRow label="Signature">
          <span style={{ color: signed ? '#e2e8f0' : '#94a3b8' }}>{signatureLabel}</span>
          {signing?.algorithm ? (
            <span style={{ color: '#94a3b8' }}> · {signing.algorithm}</span>
          ) : null}
        </MetaRow>
        <MetaRow label="Signing Key">{signing?.key_id || '—'}</MetaRow>
        <MetaRow label="Signing Provider">{signing?.provider || '—'}</MetaRow>
        <MetaRow label="Signing Authority">
          {signing?.hardware_backed ? (
            <span style={{ color: '#4ade80' }}>HSM/KMS-backed</span>
          ) : (
            // The abstraction existing is not a claim that hardware custody does.
            <span style={{ color: '#cbd5e1' }}>
              {signing?.assurance_label || 'Software key (not hardware-backed)'}
            </span>
          )}
        </MetaRow>
        <MetaRow label="Policy Snapshot">
          {policySnapshot?.present && policySnapshot?.policy_key ? (
            <>
              <span>{policySnapshot.policy_key}</span>
              {policySnapshot.policy_version != null ? (
                <span style={{ color: '#94a3b8' }}> · Version {policySnapshot.policy_version}</span>
              ) : null}
            </>
          ) : (
            <span style={{ color: '#94a3b8' }}>
              {policySnapshot?.reason || 'Not sealed in this package'}
            </span>
          )}
        </MetaRow>
        <MetaRow label="Last Verified">
          {lastVerifiedAt ? (
            new Date(lastVerifiedAt).toLocaleString()
          ) : (
            <span style={{ color: '#94a3b8' }}>Never verified</span>
          )}
        </MetaRow>
      </div>

      {policySnapshot?.present && policySnapshot?.decision ? (
        <p style={{ margin: '0.4rem 0 0', fontSize: '0.72rem', color: '#94a3b8' }}>
          Policy state preserved as it was at the time of the incident
          {policySnapshot.decision_kind === 'simulation'
            ? ' — recorded as a simulation, which predicts but authorizes nothing.'
            : `: ${policySnapshot.decision}.`}
        </p>
      ) : null}

      {signing?.warning ? (
        <p role="note" style={{ margin: '0.4rem 0 0', fontSize: '0.72rem', color: '#fbbf24' }}>
          ⚠ {signing.warning}
        </p>
      ) : null}
    </section>
  );
}

/* ── 2. Package Verification panel ────────────────────────────────── */

export function PackageVerificationPanel({
  result,
  phase,
  error,
}: {
  result?: VerificationResult | null;
  phase: VerifyPhase;
  error?: string | null;
}) {
  // VERIFYING is the only status the frontend originates, and it never implies
  // an outcome — the result arrives from the backend.
  const status = phase === 'verifying' ? 'VERIFYING' : result?.status;
  const presentation = verificationStatusPresentation(status);
  const checks = result?.checks ?? [];

  return (
    <section
      aria-label="Package verification"
      aria-busy={phase === 'verifying'}
      style={{
        padding: '0.7rem 0.75rem',
        background: 'rgba(148,163,184,0.05)',
        border: '1px solid rgba(148,163,184,0.14)',
        borderRadius: '6px',
        marginBottom: '0.9rem',
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: '0.5rem',
          marginBottom: '0.5rem',
        }}
      >
        <p className="sectionEyebrow" style={{ margin: 0 }}>Package Verification</p>
        <StatusPill label={presentation.label} variant={presentation.variant} />
      </div>

      {phase === 'verifying' ? (
        <p role="status" aria-live="polite" style={{ margin: 0, fontSize: '0.76rem', color: '#94a3b8' }}>
          Recomputing artifact hashes, rebuilding the Merkle tree and re-checking the manifest signature on the
          server…
        </p>
      ) : error ? (
        <p role="alert" style={{ margin: 0, fontSize: '0.76rem', color: '#fbbf24' }}>
          {error}
        </p>
      ) : !result ? (
        <p style={{ margin: 0, fontSize: '0.76rem', color: '#94a3b8' }}>
          This package has not been verified yet. Run Verify Integrity to check it against its stored bytes.
        </p>
      ) : (
        <>
          <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
            {checks.map((check) => {
              const mark = checkMark(String(check.status));
              return (
                <li
                  key={check.check}
                  style={{
                    display: 'flex',
                    alignItems: 'flex-start',
                    gap: '0.45rem',
                    padding: '0.22rem 0',
                    fontSize: '0.77rem',
                  }}
                >
                  <span aria-hidden="true" style={{ color: mark.color, fontWeight: 700, lineHeight: 1.4 }}>
                    {mark.glyph}
                  </span>
                  <span style={{ flex: 1 }}>
                    <span style={{ color: check.status === 'passed' ? '#e2e8f0' : '#cbd5e1' }}>
                      {check.label}
                    </span>
                    <span className="sr-only"> {mark.srLabel}</span>
                    {/* Each category reports its OWN reason. A single artifact
                        mismatch never collapses into a generic failure. */}
                    {check.detail ? (
                      <span
                        style={{
                          display: 'block',
                          color: check.status === 'failed' ? '#fca5a5' : '#94a3b8',
                          fontSize: '0.71rem',
                          marginTop: '0.1rem',
                        }}
                      >
                        {check.detail}
                      </span>
                    ) : null}
                    {check.status === 'failed' && (check.failed_artifact_paths?.length ?? 0) > 0 ? (
                      <span style={{ display: 'block', color: '#fca5a5', fontSize: '0.71rem' }}>
                        Affected: {check.failed_artifact_paths!.slice(0, 5).join(', ')}
                        {check.failed_artifact_paths!.length > 5
                          ? ` +${check.failed_artifact_paths!.length - 5} more`
                          : ''}
                      </span>
                    ) : null}
                  </span>
                </li>
              );
            })}
          </ul>
          {result.verified_at ? (
            <p style={{ margin: '0.45rem 0 0', fontSize: '0.7rem', color: '#94a3b8' }}>
              Last verified {new Date(result.verified_at).toLocaleString()}
              {result.schema_version ? ` · manifest schema ${result.schema_version}` : ''}
            </p>
          ) : null}
        </>
      )}
    </section>
  );
}

/* ── 3. VERIFIED shield ───────────────────────────────────────────── */

export function VerificationShield({
  result,
  phase,
  contract,
}: {
  result?: VerificationResult | null;
  phase: VerifyPhase;
  /** The canonical contract. When present its ``shield`` state is authoritative. */
  contract?: VerificationContract | null;
}) {
  const status = phase === 'verifying' ? 'VERIFYING' : result?.status;
  // The single gate for the green affordance: the BACKEND's shield state, which
  // is derived from the canonical verification status alone. Evidence
  // completeness never turns this green — a 100%-complete package that has not
  // been verified reads READY FOR VERIFICATION, and a failed one reads INTEGRITY
  // CHECK FAILED.
  const shieldState =
    phase === 'verifying'
      ? 'VERIFYING'
      : (contract?.shield?.state ?? (status === VERIFIED_STATUS ? 'VERIFIED' : null));
  const isVerified = shieldState === VERIFIED_STATUS;
  const shield = shieldState ? SHIELD_PRESENTATION[shieldState] : undefined;
  const presentation = shield
    ? { label: shield.label, variant: 'neutral' as PillVariant, tone: shield.tone }
    : verificationStatusPresentation(status);
  const hardwareBacked = Boolean((contract?.signer ?? result?.signer)?.hardware_backed);

  const body = (() => {
    if (phase === 'verifying') return 'Verifying this package against its stored bytes…';
    if (isVerified && hardwareBacked) {
      return 'This package is cryptographically sealed and tamper-evident. Its manifest was signed by the configured hardware-backed key.';
    }
    if (shield) return shield.body;
    if (!result) return 'This package has not been verified yet.';
    switch (status) {
      case 'VERIFICATION_FAILED':
        return 'One or more cryptographic checks FAILED. This package must not be presented as proof.';
      case 'INCOMPLETE_PACKAGE':
        return 'Evidence this package declares is missing, so it cannot be verified in full.';
      case 'SIGNATURE_UNAVAILABLE':
        return 'The manifest signature could not be checked, so this package is not verified. This is not evidence of tampering.';
      case 'PARTIALLY_VERIFIED':
        return 'Everything checkable passed, but at least one check could not be run. This package is not fully verified.';
      default:
        return 'This package is not verified.';
    }
  })();

  return (
    <section
      aria-label="Package verification result"
      style={{
        padding: '0.85rem 0.8rem',
        borderRadius: '8px',
        textAlign: 'center',
        border: `1px solid ${isVerified ? 'rgba(34,197,94,0.35)' : 'rgba(148,163,184,0.18)'}`,
        background: isVerified ? 'rgba(34,197,94,0.07)' : 'rgba(148,163,184,0.05)',
      }}
    >
      <svg
        width="46"
        height="52"
        viewBox="0 0 46 52"
        role="img"
        aria-label={presentation.label}
        style={{ display: 'block', margin: '0 auto 0.4rem' }}
      >
        <path
          d="M23 2 L43 9 V25 C43 37 34 46 23 50 C12 46 3 37 3 25 V9 Z"
          fill="none"
          stroke={presentation.tone}
          strokeWidth="2.5"
          strokeLinejoin="round"
        />
        {isVerified ? (
          <path d="M14 25 L20.5 32 L32 18" fill="none" stroke={presentation.tone} strokeWidth="3.2" strokeLinecap="round" strokeLinejoin="round" />
        ) : (
          <>
            <line x1="23" y1="17" x2="23" y2="30" stroke={presentation.tone} strokeWidth="3.2" strokeLinecap="round" />
            <circle cx="23" cy="37" r="1.9" fill={presentation.tone} />
          </>
        )}
      </svg>
      <p
        style={{
          margin: 0,
          fontWeight: 800,
          letterSpacing: '0.08em',
          fontSize: '0.86rem',
          color: presentation.tone,
        }}
      >
        {presentation.label.toUpperCase()}
      </p>
      <p style={{ margin: '0.35rem 0 0', fontSize: '0.71rem', color: '#94a3b8', lineHeight: 1.45 }}>{body}</p>
      {isVerified && !hardwareBacked ? (
        // Truthfulness: a verified seal is not a hardware-custodied signature,
        // and the product never lets one read as the other.
        <p style={{ margin: '0.35rem 0 0', fontSize: '0.68rem', color: '#94a3b8' }}>
          Sealed with a software-held key ({result?.signer?.assurance_label || 'shared-secret HMAC'}), not an
          HSM/KMS-backed signature.
        </p>
      ) : null}
    </section>
  );
}

/* ── 4. Package Contents ──────────────────────────────────────────── */

export function PackageContents({ contents }: { contents?: PackageContentEntry[] | null }) {
  const entries = contents ?? [];
  if (entries.length === 0) {
    return (
      <section aria-label="Package contents" style={{ marginTop: '0.9rem' }}>
        <p className="sectionEyebrow" style={{ margin: '0 0 0.35rem' }}>Package Contents</p>
        <p style={{ margin: 0, fontSize: '0.76rem', color: '#94a3b8' }}>
          Package contents are available once this package has a retrievable manifest.
        </p>
      </section>
    );
  }
  return (
    <section aria-label="Package contents" style={{ marginTop: '0.9rem' }}>
      <p className="sectionEyebrow" style={{ margin: '0 0 0.4rem' }}>Package Contents</p>
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fill, minmax(120px, 1fr))',
          gap: '0.5rem',
        }}
      >
        {entries.map((entry) => (
          <div
            key={entry.key}
            title={entry.available ? entry.path : entry.unavailable_reason ?? 'Not included in this package'}
            style={{
              padding: '0.5rem 0.45rem',
              borderRadius: '6px',
              textAlign: 'center',
              border: '1px solid rgba(148,163,184,0.14)',
              // An unavailable file is visibly dimmed and labelled — never
              // rendered as though it were present in the archive.
              background: entry.available ? 'rgba(148,163,184,0.06)' : 'rgba(148,163,184,0.02)',
              opacity: entry.available ? 1 : 0.55,
            }}
          >
            <div style={{ fontSize: '1.05rem', fontWeight: 700, color: entry.available ? '#e2e8f0' : '#94a3b8' }}>
              {entry.kind === 'directory' ? (entry.count ?? 0) : entry.available ? '✓' : '—'}
            </div>
            <div style={{ fontSize: '0.72rem', color: '#cbd5e1', marginTop: '0.15rem' }}>{entry.label}</div>
            <div style={{ fontSize: '0.64rem', color: '#94a3b8', wordBreak: 'break-all', marginTop: '0.1rem' }}>
              {entry.path.split('/').slice(1).join('/') || entry.path}
            </div>
            {!entry.available ? (
              <div style={{ fontSize: '0.63rem', color: '#fbbf24', marginTop: '0.2rem' }}>Unavailable</div>
            ) : null}
          </div>
        ))}
      </div>
      {entries.some((entry) => !entry.available) ? (
        <ul style={{ margin: '0.4rem 0 0', paddingLeft: '1rem', fontSize: '0.68rem', color: '#94a3b8' }}>
          {entries
            .filter((entry) => !entry.available && entry.unavailable_reason)
            .map((entry) => (
              <li key={`${entry.key}-reason`}>
                {entry.label}: {entry.unavailable_reason}
              </li>
            ))}
        </ul>
      ) : null}
    </section>
  );
}


/* ── 5. Verification Checklist (canonical) ────────────────────────── */

/**
 * The Screen 9 Verification Checklist.
 *
 * Every row comes from the backend contract's ``checklist``, which is computed
 * at READ time from the recorded verification result — never from the frozen
 * build-time completeness snapshot that made a verified package display
 * "Hashes verified ✗".
 *
 * Rows are tri-state and never lie by omission:
 *   ✓  the check ran and passed
 *   ✕  the check ran and FAILED
 *   ○  the check has not been run (never rendered as a failure)
 *   ?  the check could not be completed (e.g. no verification key)
 *   –  this package's schema never declared the fact, so there is nothing to check
 *
 * Only checks the backend actually supports appear here — the component renders
 * the rows it is given and invents none.
 */
export function VerificationChecklist({
  checklist,
  executed,
  phase,
}: {
  checklist?: VerificationChecklistItem[] | null;
  /** Whether a real server-side verification has run for this package. */
  executed?: boolean;
  phase?: VerifyPhase;
}) {
  const rows = checklist ?? [];
  if (rows.length === 0) {
    return (
      <p className="tableMeta" style={{ marginBottom: '0.75rem', fontSize: '0.72rem' }}>
        Select a completed package to see its verification checklist.
      </p>
    );
  }
  return (
    <div style={{ marginBottom: '0.75rem' }} aria-busy={phase === 'verifying'}>
      <p className="sectionEyebrow" style={{ marginBottom: '0.4rem' }}>
        Verification Checklist
      </p>
      {rows.map((item) => {
        const mark = checkMark(String(item.state ?? (item.present ? 'passed' : 'not_verified')));
        return (
          <div
            key={item.code}
            style={{
              display: 'flex',
              alignItems: 'flex-start',
              gap: '0.4rem',
              marginBottom: '0.25rem',
              fontSize: '0.75rem',
            }}
          >
            <span aria-hidden="true" style={{ color: mark.color, fontWeight: 700, lineHeight: 1.5 }}>
              {mark.glyph}
            </span>
            <span style={{ flex: 1 }}>
              <span style={{ color: item.state === 'failed' ? '#f87171' : undefined }}>
                {item.label}
              </span>
              {item.state === 'not_verified' ? (
                <span style={{ color: '#94a3b8' }}> — not verified</span>
              ) : null}
              <span className="sr-only"> {mark.srLabel}</span>
            </span>
          </div>
        );
      })}
      {executed === false ? (
        <p className="tableMeta" style={{ margin: '0.35rem 0 0', fontSize: '0.68rem' }}>
          Cryptographic checks have not been run for this package yet. Run Verify Integrity.
        </p>
      ) : null}
    </div>
  );
}
