'use client';

import { useCallback, useEffect, useState } from 'react';

import { StatusPill } from 'app/components/ui-primitives';
import { usePilotAuth } from 'app/pilot-auth-context';

import { DecodedTable } from './events-tab';
import { getJson, sendJson, watchlistUrl } from './external-watchlist-api';
import type { Finding, FindingDetailPayload, ProspectReport } from './external-watchlist-types';
import {
  EXTERNAL_SOURCE_LABEL,
  FINDING_CLASS_LABELS,
  FINDING_STATUS_OPTIONS,
  SEVERITY_LABELS,
  apiErrorMessage,
  findingStatusLabel,
  formatTimestamp,
  severityVariant,
  shortAddress,
  shortHash,
  verificationLabel,
} from './external-watchlist-view';
import ProspectReportPanel from './prospect-report-panel';

const PAGE_SIZE = 50;

export default function FindingsTab({ watchlistId }: { watchlistId: string }) {
  const { authHeaders } = usePilotAuth();
  const [findings, setFindings] = useState<Finding[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [statusFilter, setStatusFilter] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    const query = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) });
    if (statusFilter) query.set('status', statusFilter);
    try {
      const { ok, status, payload } = await getJson(watchlistUrl(watchlistId, `/findings?${query.toString()}`), authHeaders);
      if (!ok) {
        setError(apiErrorMessage(status, payload, 'Findings could not be loaded'));
        return;
      }
      setFindings((payload.findings as Finding[]) ?? []);
      setTotal(Number((payload.pagination as { total?: number } | undefined)?.total ?? 0));
    } catch {
      setError('Findings could not be loaded.');
    } finally {
      setLoading(false);
    }
  }, [authHeaders, offset, statusFilter, watchlistId]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <section data-testid="findings-tab">
      <div className="ewlFilters">
        <label>
          Status
          <select value={statusFilter} onChange={(event) => { setStatusFilter(event.target.value); setOffset(0); }}>
            <option value="">All statuses</option>
            {FINDING_STATUS_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
          </select>
        </label>
        <span className="ewlFiltersMeta">{total} finding(s) · {EXTERNAL_SOURCE_LABEL}</span>
      </div>

      {error ? <p className="statusLine" style={{ color: 'var(--danger-fg)' }}>{error}</p> : null}
      {loading ? <p className="muted">Loading…</p> : null}
      {!loading && !error && findings.length === 0 ? (
        <p className="muted" data-testid="findings-empty">
          No findings{statusFilter ? ' with this status' : ' yet'}. That is not a statement that the protocol is safe — only
          that no monitored event met a detection rule.
        </p>
      ) : null}

      {findings.length > 0 ? (
        <div className="adminTableWrap">
          <table className="adminTable ewlTable" data-testid="findings-table">
            <thead>
              <tr>
                <th>Severity</th>
                <th>Type</th>
                <th>Target</th>
                <th>Transaction</th>
                <th>Block</th>
                <th>Observed time</th>
                <th>Initiating wallet</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {findings.map((finding) => (
                <tr key={finding.id} className="ewlRow ewlRowClickable" data-testid="finding-row" onClick={() => setOpenId(finding.id)}>
                  <td><StatusPill label={SEVERITY_LABELS[finding.severity] ?? finding.severity} variant={severityVariant(finding.severity)} /></td>
                  <td className="ewlWrapCell">
                    <button type="button" className="ewlLinkButton ewlStrong" onClick={() => setOpenId(finding.id)}>{finding.title}</button>
                    <span className="adminContactMembers">{finding.finding_class_label ?? FINDING_CLASS_LABELS[finding.finding_class]}</span>
                  </td>
                  <td>
                    {finding.target_label ?? shortAddress(finding.target_address)}
                    {finding.target_type ? <span className="adminContactMembers">{finding.target_type}</span> : null}
                  </td>
                  <td className="ewlMono">
                    {finding.tx_explorer_url && finding.tx_hash ? (
                      <a href={finding.tx_explorer_url} target="_blank" rel="noopener noreferrer nofollow" title={finding.tx_hash}
                        onClick={(event) => event.stopPropagation()}>
                        {shortHash(finding.tx_hash)}
                      </a>
                    ) : '—'}
                  </td>
                  <td>{finding.block_number != null ? `#${finding.block_number.toLocaleString('en-US')}` : '—'}</td>
                  <td>{formatTimestamp(finding.observed_at)}</td>
                  <td className="ewlMono" title={finding.initiator ?? undefined}>{finding.initiator ? shortAddress(finding.initiator) : '—'}</td>
                  <td>{finding.status_label ?? findingStatusLabel(finding.status)}</td>
                </tr>
              ))}
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

      {openId ? (
        <FindingDetail watchlistId={watchlistId} findingId={openId} onClose={() => setOpenId(null)} onChanged={load} />
      ) : null}
    </section>
  );
}

function StateBlock({ title, value }: { title: string; value: Record<string, unknown> | null | undefined }) {
  return (
    <div className="ewlState">
      <p className="drawerMetaLabel">{title}</p>
      {value ? <DecodedTable values={value} /> : <p className="muted ewlSmall">Not available from public telemetry.</p>}
    </div>
  );
}

/**
 * One finding in full. Labelled "External Public Monitoring" throughout, and
 * the AI investigation is shown as its three separate statements — never
 * merged into a single verdict.
 */
function FindingDetail({ watchlistId, findingId, onClose, onChanged }: {
  watchlistId: string;
  findingId: string;
  onClose: () => void;
  onChanged: () => Promise<void> | void;
}) {
  const { authHeaders, csrfReady, refreshCsrfToken } = usePilotAuth();
  const [detail, setDetail] = useState<FindingDetailPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [nextStatus, setNextStatus] = useState('');
  const [note, setNote] = useState('');
  const [report, setReport] = useState<ProspectReport | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const { ok, status, payload } = await getJson(watchlistUrl(watchlistId, `/findings/${findingId}`), authHeaders);
      if (!ok) {
        setError(apiErrorMessage(status, payload, 'This finding could not be loaded'));
        return;
      }
      const loaded = payload as unknown as FindingDetailPayload;
      setDetail(loaded);
      setNextStatus(loaded.finding.status);
    } catch {
      setError('This finding could not be loaded.');
    }
  }, [authHeaders, findingId, watchlistId]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape' && !report) onClose();
    }
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose, report]);

  async function act(label: string, request: () => ReturnType<typeof sendJson>, success: string) {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const result = await request();
      if (!result.ok) {
        setError(apiErrorMessage(result.status, result.payload, `${label} failed`));
        return null;
      }
      setMessage(success);
      await load();
      await onChanged();
      return result.payload;
    } catch {
      setError(`${label} failed.`);
      return null;
    } finally {
      setBusy(false);
    }
  }

  const finding = detail?.finding;
  const analysis = finding?.ai_analysis ?? {};

  return (
    <div className="drawerOverlay" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <aside className="drawerCard ewlDrawer" role="dialog" aria-modal="true" aria-label="Finding detail" data-testid="finding-detail">
        <div className="ewlSectionHeader">
          <span className="ewlSourceLabel" data-testid="external-source-label">{EXTERNAL_SOURCE_LABEL}</span>
          <button type="button" className="btn btn-ghost" onClick={onClose}>Close</button>
        </div>
        {!detail && !error ? <p className="muted">Loading…</p> : null}
        {error ? <p className="statusLine" style={{ color: 'var(--danger-fg)' }}>{error}</p> : null}
        {finding ? (
          <>
            <h2 className="ewlDrawerTitle">{finding.title}</h2>
            <p className="muted ewlSmall">
              {finding.finding_class_label ?? FINDING_CLASS_LABELS[finding.finding_class]} · {detail?.protocol.name} ·{' '}
              <StatusPill label={SEVERITY_LABELS[finding.severity] ?? finding.severity} variant={severityVariant(finding.severity)} />
            </p>
            <p className="ewlExplanation">{finding.explanation}</p>

            <div className="drawerMetaGrid">
              <div><span className="drawerMetaLabel">Observed event</span><span>{detail?.related_telemetry[0]?.event_name ?? '—'}</span></div>
              <div><span className="drawerMetaLabel">Network</span><span>{finding.network.label}</span></div>
              <div>
                <span className="drawerMetaLabel">Transaction hash</span>
                {finding.tx_explorer_url && finding.tx_hash ? (
                  <a className="ewlMono ewlBreak" href={finding.tx_explorer_url} target="_blank" rel="noopener noreferrer nofollow">{finding.tx_hash}</a>
                ) : <span>—</span>}
              </div>
              <div><span className="drawerMetaLabel">Block</span><span>{finding.block_number != null ? `#${finding.block_number.toLocaleString('en-US')}` : '—'}</span></div>
              <div><span className="drawerMetaLabel">Initiator</span><span className="ewlMono ewlBreak">{finding.initiator ?? 'Not recorded'}</span></div>
              <div>
                <span className="drawerMetaLabel">Affected contract</span>
                {finding.contract_explorer_url && finding.contract_address ? (
                  <a className="ewlMono ewlBreak" href={finding.contract_explorer_url} target="_blank" rel="noopener noreferrer nofollow">{finding.contract_address}</a>
                ) : <span className="ewlMono">{finding.contract_address ?? '—'}</span>}
              </div>
              <div><span className="drawerMetaLabel">Observed time</span><span>{formatTimestamp(finding.observed_at)}</span></div>
              <div><span className="drawerMetaLabel">Execution authority</span><span>None (read-only)</span></div>
            </div>

            <section className="drawerSection">
              <h3 className="drawerSectionTitle">Decoded parameters</h3>
              <DecodedTable values={finding.decoded} />
            </section>

            <section className="drawerSection ewlStates">
              <StateBlock title="Previous state" value={finding.previous_state} />
              <StateBlock title="New state" value={finding.new_state} />
            </section>

            <section className="drawerSection">
              <h3 className="drawerSectionTitle">Related telemetry</h3>
              {detail?.related_telemetry.length ? (
                <ul className="ewlActivity">
                  {detail.related_telemetry.map((event) => (
                    <li key={event.id}>
                      <span className="ewlActivityName">{event.event_name}</span>
                      <span className="muted">log #{event.log_index}</span>
                      <span className="muted ewlMono" title={event.payload_sha256}>sha256 {event.payload_sha256.slice(0, 12)}…</span>
                    </li>
                  ))}
                </ul>
              ) : <p className="muted ewlSmall">No stored telemetry for this transaction.</p>}
            </section>

            <section className="drawerSection" data-testid="ai-investigation">
              <h3 className="drawerSectionTitle">AI Investigation</h3>
              <div className="ewlAnalysis">
                <div><p className="drawerMetaLabel">1 · Observed fact</p><p>{analysis.observed_fact ?? '—'}</p></div>
                <div><p className="drawerMetaLabel">2 · Decoda interpretation</p><p>{analysis.decoda_interpretation ?? '—'}</p></div>
                <div><p className="drawerMetaLabel">3 · Operational authorization</p><p>{analysis.operational_authorization ?? '—'}</p></div>
              </div>
              <p className="muted ewlSmall">Source: {analysis.source === 'ai' ? 'AI model (validated against the facts above)' : 'Deterministic analysis'}</p>
            </section>

            <section className="drawerSection" data-testid="evidence-integrity">
              <h3 className="drawerSectionTitle">Evidence integrity</h3>
              {detail?.evidence_integrity ? (
                <>
                  <p>
                    <StatusPill label={detail.evidence_integrity.valid ? 'Verified' : 'Verification failed'}
                      variant={detail.evidence_integrity.valid ? 'success' : 'danger'} />{' '}
                    {verificationLabel({ ...detail.evidence_integrity, status: detail.evidence_integrity.status })}
                  </p>
                  <p className="muted ewlSmall">Evidence SHA-256 <span className="ewlMono ewlBreak">{detail.evidence_integrity.evidence_sha256}</span></p>
                </>
              ) : <p className="muted ewlSmall">No evidence package generated yet.</p>}
            </section>

            <section className="drawerSection">
              <h3 className="drawerSectionTitle">Review</h3>
              <div className="ewlInlineControl ewlWrap">
                <select aria-label="Finding status" value={nextStatus} onChange={(event) => setNextStatus(event.target.value)}>
                  {FINDING_STATUS_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                </select>
                <input aria-label="Internal note" placeholder="Internal note (optional)" value={note} maxLength={1000}
                  onChange={(event) => setNote(event.target.value)} />
                <button type="button" className="btn btn-secondary" disabled={!csrfReady || busy || nextStatus === finding.status && !note}
                  onClick={() => void act('Status change', () => sendJson(watchlistUrl(watchlistId, `/findings/${findingId}`), 'PATCH',
                    { status: nextStatus, note: note || undefined }, authHeaders, refreshCsrfToken), 'Status saved.').then(() => setNote(''))}>
                  Save status
                </button>
              </div>
              <div className="ewlDrawerActions">
                <button type="button" className="btn btn-secondary" data-testid="mark-outreach-candidate"
                  disabled={!csrfReady || busy || finding.status === 'outreach_candidate'}
                  onClick={() => void act('Mark as Outreach Candidate', () => sendJson(watchlistUrl(watchlistId, `/findings/${findingId}`), 'PATCH',
                    { status: 'outreach_candidate' }, authHeaders, refreshCsrfToken), 'Marked as outreach candidate (internal only).')}>
                  Mark as Outreach Candidate
                </button>
                <button type="button" className="btn btn-secondary" disabled={!csrfReady || busy}
                  onClick={() => void act('Evidence package', () => sendJson(watchlistUrl(watchlistId, `/findings/${findingId}/evidence`), 'POST',
                    {}, authHeaders, refreshCsrfToken), 'Evidence package generated and verified.')}>
                  Generate Evidence Package
                </button>
                <button type="button" className="btn btn-primary" data-testid="generate-prospect-report" disabled={!csrfReady || busy}
                  onClick={() => void act('Prospect report', () => sendJson(watchlistUrl(watchlistId, `/findings/${findingId}/prospect-report`), 'POST',
                    {}, authHeaders, refreshCsrfToken), 'Prospect report generated.').then((payload) => {
                    if (payload?.report) setReport(payload.report as ProspectReport);
                  })}>
                  Generate Prospect Report
                </button>
              </div>
              <p className="muted ewlSmall">
                Status and outreach marks are internal only and never appear in a prospect report. There are no response
                actions for external findings.
              </p>
              {message ? <p className="statusLine ewlSuccess" role="status">{message}</p> : null}
            </section>
          </>
        ) : null}
      </aside>
      {report ? <ProspectReportPanel report={report} onClose={() => setReport(null)} /> : null}
    </div>
  );
}
