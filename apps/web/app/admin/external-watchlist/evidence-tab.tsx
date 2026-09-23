'use client';

import { useCallback, useEffect, useState } from 'react';

import { StatusPill } from 'app/components/ui-primitives';
import { usePilotAuth } from 'app/pilot-auth-context';

import { getJson, watchlistUrl } from './external-watchlist-api';
import type { EvidenceRow, EvidenceVerification, ProspectReport } from './external-watchlist-types';
import { EVIDENCE_DISCLAIMER, apiErrorMessage, formatTimestamp, shortHash, verificationLabel } from './external-watchlist-view';
import ProspectReportPanel from './prospect-report-panel';

const PAGE_SIZE = 50;

/**
 * Sealed evidence packages and prospect reports for this protocol. "Verify"
 * re-checks the stored package on the server (file hashes, manifest hash,
 * HMAC seal, and an Ed25519 signature when present); nothing is assumed from a
 * previous check.
 */
export default function EvidenceTab({ watchlistId }: { watchlistId: string }) {
  const { authHeaders } = usePilotAuth();
  const [rows, setRows] = useState<EvidenceRow[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [verifications, setVerifications] = useState<Record<string, EvidenceVerification>>({});
  const [report, setReport] = useState<ProspectReport | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const { ok, status, payload } = await getJson(
        watchlistUrl(watchlistId, `/evidence?limit=${PAGE_SIZE}&offset=${offset}`), authHeaders,
      );
      if (!ok) {
        setError(apiErrorMessage(status, payload, 'Evidence could not be loaded'));
        return;
      }
      setRows((payload.evidence as EvidenceRow[]) ?? []);
      setTotal(Number((payload.pagination as { total?: number } | undefined)?.total ?? 0));
    } catch {
      setError('Evidence could not be loaded.');
    } finally {
      setLoading(false);
    }
  }, [authHeaders, offset, watchlistId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function fetchPackage(row: EvidenceRow) {
    setBusyId(row.id);
    try {
      const { ok, status, payload } = await getJson(watchlistUrl(watchlistId, `/evidence/${row.id}`), authHeaders);
      if (!ok) {
        setError(apiErrorMessage(status, payload, 'The package could not be read'));
        return null;
      }
      const evidence = payload.evidence as (EvidenceRow & { verification: EvidenceVerification }) | undefined;
      if (evidence?.verification) setVerifications((previous) => ({ ...previous, [row.id]: evidence.verification }));
      return payload;
    } catch {
      setError('The package could not be read.');
      return null;
    } finally {
      setBusyId(null);
    }
  }

  async function download(row: EvidenceRow) {
    const payload = await fetchPackage(row);
    if (!payload) return;
    const blob = new Blob([JSON.stringify(payload.evidence, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `decoda-external-${row.package_type}-${row.evidence_sha256.slice(0, 12)}.json`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  return (
    <section data-testid="evidence-tab">
      <p className="ewlNotice" data-testid="evidence-disclaimer">{EVIDENCE_DISCLAIMER}</p>
      {error ? <p className="statusLine" style={{ color: 'var(--danger-fg)' }}>{error}</p> : null}
      {loading ? <p className="muted">Loading…</p> : null}
      {!loading && !error && rows.length === 0 ? (
        <p className="muted" data-testid="evidence-empty">
          No evidence generated yet. Open a finding and choose &ldquo;Generate Evidence Package&rdquo; or &ldquo;Generate Prospect Report&rdquo;.
        </p>
      ) : null}
      {rows.length > 0 ? (
        <div className="adminTableWrap">
          <table className="adminTable ewlTable">
            <thead>
              <tr>
                <th>Generated</th>
                <th>Type</th>
                <th>Finding</th>
                <th>Evidence SHA-256</th>
                <th>Manifest SHA-256</th>
                <th>Seal</th>
                <th>Verification</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const verification = verifications[row.id];
                return (
                  <tr key={row.id} data-testid="evidence-row">
                    <td>{formatTimestamp(row.generated_at)}</td>
                    <td>{row.package_type === 'prospect_report' ? 'Prospect report' : 'Evidence package'}</td>
                    <td className="ewlWrapCell">
                      {row.finding_title ?? '—'}
                      {row.finding_tx_hash ? <span className="adminContactMembers ewlMono">{shortHash(row.finding_tx_hash)}</span> : null}
                    </td>
                    <td className="ewlMono" title={row.evidence_sha256}>{shortHash(row.evidence_sha256)}</td>
                    <td className="ewlMono" title={row.manifest_sha256}>{shortHash(row.manifest_sha256)}</td>
                    <td>{row.signature_algorithm ?? '—'}</td>
                    <td>
                      {verification ? (
                        <span title={verification.errors.join(', ') || undefined}>
                          <StatusPill label={verification.valid ? 'Verified' : 'Failed'} variant={verification.valid ? 'success' : 'danger'} />
                          <span className="adminContactMembers">{verificationLabel(verification)}</span>
                        </span>
                      ) : <span className="muted">Not checked</span>}
                    </td>
                    <td>
                      <div className="adminRowActions">
                        <button type="button" className="btn btn-secondary" disabled={busyId === row.id} onClick={() => void fetchPackage(row)}>Verify</button>
                        <button type="button" className="btn btn-secondary" disabled={busyId === row.id} onClick={() => void download(row)}>Download JSON</button>
                        {row.package_type === 'prospect_report' ? (
                          <button type="button" className="btn btn-secondary" disabled={busyId === row.id}
                            onClick={async () => {
                              const payload = await fetchPackage(row);
                              if (payload?.report) setReport(payload.report as ProspectReport);
                            }}>
                            View report
                          </button>
                        ) : null}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : null}
      {total > PAGE_SIZE ? (
        <div className="ewlPager">
          <button type="button" className="btn btn-secondary" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>Previous</button>
          <span className="muted">{offset + 1}–{Math.min(offset + PAGE_SIZE, total)} of {total}</span>
          <button type="button" className="btn btn-secondary" disabled={offset + PAGE_SIZE >= total} onClick={() => setOffset(offset + PAGE_SIZE)}>Next</button>
        </div>
      ) : null}
      {report ? <ProspectReportPanel report={report} onClose={() => setReport(null)} /> : null}
    </section>
  );
}
