'use client';

import { Fragment, useCallback, useEffect, useState } from 'react';

import { usePilotAuth } from 'app/pilot-auth-context';

import { getJson, watchlistUrl } from './external-watchlist-api';
import type { ObservedEvent, WatchlistTarget } from './external-watchlist-types';
import {
  EVENT_CATEGORY_LABELS,
  INGEST_SOURCE_LABELS,
  apiErrorMessage,
  formatTimestamp,
  shortAddress,
  shortHash,
} from './external-watchlist-view';

const PAGE_SIZE = 50;

/**
 * Every public log stored for this protocol, newest first. A row is one
 * on-chain log, stored once. "Historical backfill" and "Live poll" are both
 * real chain data; the column says which path read it, and an estimated block
 * time is labelled as estimated rather than shown as exact.
 */
export default function EventsTab({ watchlistId, targets, initialTargetId = '' }: {
  watchlistId: string;
  targets: WatchlistTarget[];
  initialTargetId?: string;
}) {
  const { authHeaders } = usePilotAuth();
  const [events, setEvents] = useState<ObservedEvent[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [category, setCategory] = useState('');
  const [targetId, setTargetId] = useState(initialTargetId);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    const query = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) });
    if (category) query.set('category', category);
    if (targetId) query.set('target_id', targetId);
    try {
      const { ok, status, payload } = await getJson(watchlistUrl(watchlistId, `/events?${query.toString()}`), authHeaders);
      if (!ok) {
        setError(apiErrorMessage(status, payload, 'Events could not be loaded'));
        return;
      }
      setEvents((payload.events as ObservedEvent[]) ?? []);
      setTotal(Number((payload.pagination as { total?: number } | undefined)?.total ?? 0));
    } catch {
      setError('Events could not be loaded.');
    } finally {
      setLoading(false);
    }
  }, [authHeaders, category, offset, targetId, watchlistId]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <section data-testid="events-tab">
      <div className="ewlFilters">
        <label>
          Category
          <select value={category} onChange={(event) => { setCategory(event.target.value); setOffset(0); }}>
            <option value="">All categories</option>
            {Object.entries(EVENT_CATEGORY_LABELS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
          </select>
        </label>
        <label>
          Target
          <select value={targetId} onChange={(event) => { setTargetId(event.target.value); setOffset(0); }}>
            <option value="">All targets</option>
            {targets.map((target) => (
              <option key={target.id} value={target.id}>{target.label ?? shortAddress(target.address)}</option>
            ))}
          </select>
        </label>
        <span className="ewlFiltersMeta">{total} event(s)</span>
      </div>

      {error ? <p className="statusLine" style={{ color: 'var(--danger-fg)' }}>{error}</p> : null}
      {loading ? <p className="muted">Loading…</p> : null}
      {!loading && !error && events.length === 0 ? (
        <p className="muted" data-testid="events-empty">
          No events observed{category || targetId ? ' for these filters' : ' yet'}. No data is not a clean bill of health —
          it means no matching public event has been read.
        </p>
      ) : null}

      {events.length > 0 ? (
        <div className="adminTableWrap">
          <table className="adminTable ewlTable">
            <thead>
              <tr>
                <th>Observed</th>
                <th>Event</th>
                <th>Category</th>
                <th>Contract</th>
                <th>Transaction</th>
                <th>Block</th>
                <th>Initiator</th>
                <th>Source</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {events.map((event) => (
                <Fragment key={event.id}>
                  <tr data-testid="event-row">
                    <td>
                      {formatTimestamp(event.observed_at)}
                      {event.observed_at_source !== 'block_timestamp' ? (
                        <span className="adminContactMembers">estimated from block height</span>
                      ) : null}
                    </td>
                    <td>
                      {event.event_name}
                      {event.decode_status !== 'decoded' ? <span className="adminContactMembers">{event.decode_status}</span> : null}
                    </td>
                    <td>{EVENT_CATEGORY_LABELS[event.event_category] ?? event.event_category}</td>
                    <td className="ewlMono" title={event.contract_address}>{shortAddress(event.contract_address)}</td>
                    <td className="ewlMono">
                      {event.tx_explorer_url ? (
                        <a href={event.tx_explorer_url} target="_blank" rel="noopener noreferrer nofollow" title={event.tx_hash}>
                          {shortHash(event.tx_hash)}
                        </a>
                      ) : shortHash(event.tx_hash)}
                    </td>
                    <td>#{event.block_number.toLocaleString('en-US')}</td>
                    <td className="ewlMono" title={event.initiator ?? undefined}>{event.initiator ? shortAddress(event.initiator) : '—'}</td>
                    <td>{INGEST_SOURCE_LABELS[event.ingest_source] ?? event.ingest_source}</td>
                    <td>
                      <button type="button" className="ewlLinkButton" onClick={() => setOpen(open === event.id ? null : event.id)}>
                        {open === event.id ? 'Hide' : 'Decoded'}
                      </button>
                    </td>
                  </tr>
                  {open === event.id ? (
                    <tr className="ewlExpandedRow">
                      <td colSpan={9}>
                        <DecodedTable values={event.decoded} />
                        <p className="muted ewlSmall">Payload SHA-256 <span className="ewlMono">{event.payload_sha256}</span></p>
                      </td>
                    </tr>
                  ) : null}
                </Fragment>
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
    </section>
  );
}

export function DecodedTable({ values }: { values: Record<string, unknown> | null | undefined }) {
  const entries = Object.entries(values ?? {}).filter(([, value]) => value !== null && value !== undefined);
  if (entries.length === 0) return <p className="muted ewlSmall">No decoded parameters.</p>;
  return (
    <dl className="ewlKv">
      {entries.map(([key, value]) => (
        <div key={key}>
          <dt>{key.replace(/_/g, ' ')}</dt>
          <dd className="ewlMono">{typeof value === 'object' ? JSON.stringify(value) : String(value)}</dd>
        </div>
      ))}
    </dl>
  );
}
