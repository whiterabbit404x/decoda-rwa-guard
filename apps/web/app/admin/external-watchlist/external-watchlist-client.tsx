'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useCallback, useEffect, useState } from 'react';

import { StatusPill } from 'app/components/ui-primitives';
import { usePilotAuth } from 'app/pilot-auth-context';

import AddProtocolDialog from './add-protocol-dialog';
import { EXTERNAL_WATCHLIST_API, getJson, sendJson } from './external-watchlist-api';
import { ConsoleGate, useExternalWatchlistConsole, type ExternalWatchlistFeature } from './external-watchlist-console';
import {
  WATCHLIST_NOTICE,
  WATCHLIST_STATUS_OPTIONS,
  apiErrorMessage,
  backfillProgressLabel,
  clampPercent,
  countLabel,
  formatRelativeTime,
  formatTimestamp,
  latestEventLabel,
  latestFindingLabel,
  networkLabels,
  severityVariant,
  statusLabel,
  statusVariant,
  type WatchlistSummary,
} from './external-watchlist-view';

const PAGE_SIZE = 25;

type Filters = { q: string; network: string; status: string };
const EMPTY_FILTERS: Filters = { q: '', network: '', status: '' };

export default function ExternalWatchlistClient() {
  const consoleState = useExternalWatchlistConsole();
  return <ConsoleGate state={consoleState}>{(feature) => <WatchlistDirectory feature={feature} />}</ConsoleGate>;
}

function WatchlistDirectory({ feature }: { feature: ExternalWatchlistFeature }) {
  const router = useRouter();
  const { authHeaders, csrfReady, isAuthenticated, refreshCsrfToken } = usePilotAuth();
  const [rows, setRows] = useState<WatchlistSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS);
  const [search, setSearch] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [heartbeat, setHeartbeat] = useState<string | null>(null);
  const now = new Date();

  useEffect(() => {
    if (isAuthenticated && !csrfReady) void refreshCsrfToken().catch(() => undefined);
  }, [csrfReady, isAuthenticated, refreshCsrfToken]);

  // Search is applied after a short pause so each keystroke is not a request.
  useEffect(() => {
    const handle = window.setTimeout(() => {
      setFilters((previous) => (previous.q === search.trim() ? previous : { ...previous, q: search.trim() }));
      setOffset(0);
    }, 300);
    return () => window.clearTimeout(handle);
  }, [search]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    const query = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) });
    if (filters.q) query.set('q', filters.q);
    if (filters.network) query.set('network', filters.network);
    if (filters.status) query.set('status', filters.status);
    try {
      const { ok, status, payload } = await getJson(`${EXTERNAL_WATCHLIST_API}?${query.toString()}`, authHeaders);
      if (!ok) {
        setError(apiErrorMessage(status, payload, 'The watchlist could not be loaded'));
        setRows([]);
        return;
      }
      setRows((payload.watchlists as WatchlistSummary[]) ?? []);
      setTotal(Number((payload.pagination as { total?: number } | undefined)?.total ?? 0));
      setHeartbeat((payload.worker_heartbeat_at as string | null) ?? null);
    } catch {
      setError('The watchlist could not be loaded.');
    } finally {
      setLoading(false);
    }
  }, [authHeaders, filters, offset]);

  useEffect(() => {
    void load();
  }, [load]);

  async function createProtocol(payload: Record<string, unknown>) {
    setSubmitting(true);
    setSubmitError(null);
    try {
      const result = await sendJson(EXTERNAL_WATCHLIST_API, 'POST', payload, authHeaders, refreshCsrfToken);
      if (!result.ok) {
        setSubmitError(apiErrorMessage(result.status, result.payload, 'The protocol could not be added'));
        return;
      }
      const created = (result.payload.watchlist as { id?: string } | undefined)?.id;
      setAdding(false);
      if (created) {
        router.push(`/admin/external-watchlist/${created}`);
      } else {
        await load();
      }
    } catch {
      setSubmitError('The protocol could not be added.');
    } finally {
      setSubmitting(false);
    }
  }

  const filtered = Boolean(filters.q || filters.network || filters.status);
  const networks = feature.networks ?? [];

  return (
    <main className="adminConsole ewlPage" data-testid="external-watchlist-page">
      <div className="ewlPageHeader">
        <div>
          <p className="sectionEyebrow">Decoda internal · Founder console</p>
          <h1 className="ewlTitle">External Watchlist</h1>
          <p className="ewlSubtitle">Independent monitoring of publicly available RWA blockchain infrastructure.</p>
        </div>
        <button
          type="button"
          className="btn btn-primary"
          data-testid="monitor-public-protocol"
          disabled={!csrfReady}
          onClick={() => {
            setSubmitError(null);
            setAdding(true);
          }}
        >
          + Monitor Public Protocol
        </button>
      </div>

      <div className="ewlNotice" role="note" data-testid="external-watchlist-notice">
        <strong>Public data only.</strong> {feature.notices?.watchlist ?? WATCHLIST_NOTICE}
      </div>

      <div className="ewlFilters" role="search">
        <label>
          Protocol
          <input
            type="search"
            value={search}
            placeholder="Search by name or website"
            data-testid="filter-protocol"
            onChange={(event) => setSearch(event.target.value)}
          />
        </label>
        <label>
          Network
          <select
            value={filters.network}
            data-testid="filter-network"
            onChange={(event) => {
              setFilters((previous) => ({ ...previous, network: event.target.value }));
              setOffset(0);
            }}
          >
            <option value="">All networks</option>
            {networks.map((network) => (
              <option key={network.key} value={network.key}>{network.label}</option>
            ))}
          </select>
        </label>
        <label>
          Status
          <select
            value={filters.status}
            data-testid="filter-status"
            onChange={(event) => {
              setFilters((previous) => ({ ...previous, status: event.target.value }));
              setOffset(0);
            }}
          >
            <option value="">All statuses</option>
            {WATCHLIST_STATUS_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>{option.label}</option>
            ))}
          </select>
        </label>
        {filtered ? (
          <button type="button" className="btn btn-ghost" onClick={() => {
            setSearch('');
            setFilters(EMPTY_FILTERS);
            setOffset(0);
          }}>
            Clear filters
          </button>
        ) : null}
        <span className="ewlFiltersMeta" title={heartbeat ? formatTimestamp(heartbeat) : undefined}>
          Worker heartbeat: {heartbeat ? formatRelativeTime(heartbeat) : 'not reported'}
        </span>
      </div>

      {error ? <p className="statusLine" style={{ color: 'var(--danger-fg)' }} role="alert">{error}</p> : null}
      {loading ? <p className="muted">Loading…</p> : null}

      {!loading && !error && rows.length === 0 ? (
        <div className="ewlEmpty" data-testid="external-watchlist-empty">
          {filtered ? (
            <p>No protocols match these filters.</p>
          ) : (
            <>
              <p className="ewlEmptyTitle">No public protocols are being monitored yet.</p>
              <p className="muted">
                Add a protocol&apos;s public contract, wallet, multisig or oracle address to start independent,
                read-only monitoring. Nothing is monitored until you do.
              </p>
              <button type="button" className="btn btn-secondary" disabled={!csrfReady} onClick={() => setAdding(true)}>
                + Monitor Public Protocol
              </button>
            </>
          )}
        </div>
      ) : null}

      {rows.length > 0 ? (
        <div className="adminTableWrap">
          <table className="adminTable ewlTable" data-testid="external-watchlist-table">
            <thead>
              <tr>
                <th>Protocol</th>
                <th>Network</th>
                <th>Monitored</th>
                <th>Status</th>
                <th>Latest observed event</th>
                <th>Latest finding</th>
                <th>Last scanned</th>
                <th>Created</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id} className="ewlRow" data-testid="external-watchlist-row">
                  <td>
                    <Link href={`/admin/external-watchlist/${row.id}`} prefetch={false} className="ewlRowLink">
                      {row.name}
                    </Link>
                    {row.website_url ? <span className="adminContactMembers">{row.website_url}</span> : null}
                  </td>
                  <td>{networkLabels(row.networks)}</td>
                  <td>
                    <span className="ewlCount">{countLabel(row.contract_count, 'contract', 'contracts')}</span>
                    <span className="adminContactMembers">{countLabel(row.wallet_count, 'wallet', 'wallets')}</span>
                  </td>
                  <td>
                    <span title={row.status_reason_label ?? undefined}>
                      <StatusPill label={statusLabel(row.status)} variant={statusVariant(row.status)} />
                    </span>
                    {row.status === 'backfilling' && row.backfill ? (
                      <span className="ewlInlineProgress">
                        <span className="ewlProgress" aria-hidden="true">
                          <span className="ewlProgressFill" style={{ width: `${clampPercent(row.backfill.percent)}%` }} />
                        </span>
                        <span className="adminContactMembers">{backfillProgressLabel(row.backfill)}</span>
                      </span>
                    ) : row.status_reason_label && row.status !== 'live' ? (
                      <span className="adminContactMembers">{row.status_reason_label}</span>
                    ) : null}
                  </td>
                  <td title={row.latest_event?.observed_at ? formatTimestamp(row.latest_event.observed_at) : undefined}>
                    {latestEventLabel(row.latest_event, now)}
                  </td>
                  <td className="ewlWrapCell">
                    {row.latest_finding ? (
                      <>
                        <StatusPill label={row.latest_finding.severity ?? '—'} variant={severityVariant(row.latest_finding.severity)} />{' '}
                        {latestFindingLabel(row.latest_finding)}
                      </>
                    ) : (
                      <span className="muted">{latestFindingLabel(null)}</span>
                    )}
                  </td>
                  <td title={row.last_scanned_at ? formatTimestamp(row.last_scanned_at) : undefined}>
                    {formatRelativeTime(row.last_scanned_at, now)}
                  </td>
                  <td>{formatTimestamp(row.created_at).slice(0, 10)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {total > PAGE_SIZE ? (
        <div className="ewlPager">
          <button type="button" className="btn btn-secondary" disabled={offset === 0}
            onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>Previous</button>
          <span className="muted">
            {offset + 1}–{Math.min(offset + PAGE_SIZE, total)} of {total}
          </span>
          <button type="button" className="btn btn-secondary" disabled={offset + PAGE_SIZE >= total}
            onClick={() => setOffset(offset + PAGE_SIZE)}>Next</button>
        </div>
      ) : null}

      {adding ? (
        <AddProtocolDialog
          networks={networks}
          profiles={feature.detection_profiles}
          submitting={submitting}
          serverError={submitError}
          canSubmit={csrfReady}
          onSubmit={(payload) => void createProtocol(payload)}
          onClose={() => setAdding(false)}
        />
      ) : null}
    </main>
  );
}
