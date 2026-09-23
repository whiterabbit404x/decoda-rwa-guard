'use client';

import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { Suspense, useCallback, useEffect, useState } from 'react';

import { MetricTile, StatusPill, TabStrip } from 'app/components/ui-primitives';
import { usePilotAuth } from 'app/pilot-auth-context';

import ConvertToPilotDialog from '../convert-to-pilot-dialog';
import EventsTab from '../events-tab';
import EvidenceTab from '../evidence-tab';
import { getJson, sendJson, watchlistUrl } from '../external-watchlist-api';
import { ConsoleGate, useExternalWatchlistConsole, type ExternalWatchlistFeature } from '../external-watchlist-console';
import type { WatchlistDetail } from '../external-watchlist-types';
import {
  DETAIL_NOTICE,
  EVENT_CATEGORY_LABELS,
  PUBLIC_INFRASTRUCTURE_BADGE,
  apiErrorMessage,
  backfillProgressLabel,
  backfillStatusLabel,
  clampPercent,
  formatRelativeTime,
  formatTimestamp,
  networkLabels,
  statusLabel,
  statusVariant,
} from '../external-watchlist-view';
import FindingsTab from '../findings-tab';
import TargetsTab from '../targets-tab';

const TABS = [
  { key: 'overview', label: 'Overview' },
  { key: 'targets', label: 'Targets' },
  { key: 'events', label: 'Events' },
  { key: 'findings', label: 'Findings' },
  { key: 'evidence', label: 'Evidence' },
] as const;
type TabKey = (typeof TABS)[number]['key'];

function isTab(value: string | null): value is TabKey {
  return TABS.some((tab) => tab.key === value);
}

export default function ExternalWatchlistDetailClient({ watchlistId }: { watchlistId: string }) {
  const consoleState = useExternalWatchlistConsole();
  return (
    <ConsoleGate state={consoleState}>
      {(feature) => (
        <Suspense fallback={<main className="adminConsole"><p className="muted">Loading…</p></main>}>
          <ProtocolDetail watchlistId={watchlistId} feature={feature} />
        </Suspense>
      )}
    </ConsoleGate>
  );
}

function ProtocolDetail({ watchlistId, feature }: { watchlistId: string; feature: ExternalWatchlistFeature }) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { authHeaders, csrfReady, isAuthenticated, refreshCsrfToken } = usePilotAuth();
  const initialTab = searchParams.get('tab');
  const [tab, setTab] = useState<TabKey>(isTab(initialTab) ? initialTab : 'overview');
  const [detail, setDetail] = useState<WatchlistDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [busy, setBusy] = useState(false);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [converting, setConverting] = useState(false);
  const [backfillDays, setBackfillDays] = useState(30);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [eventsTargetId, setEventsTargetId] = useState('');

  useEffect(() => {
    if (isAuthenticated && !csrfReady) void refreshCsrfToken().catch(() => undefined);
  }, [csrfReady, isAuthenticated, refreshCsrfToken]);

  const load = useCallback(async () => {
    setError(null);
    try {
      const { ok, status, payload } = await getJson(watchlistUrl(watchlistId), authHeaders);
      if (status === 404) {
        setNotFound(true);
        return;
      }
      if (!ok) {
        setError(apiErrorMessage(status, payload, 'This protocol could not be loaded'));
        return;
      }
      setDetail(payload as unknown as WatchlistDetail);
    } catch {
      setError('This protocol could not be loaded.');
    } finally {
      setLoading(false);
    }
  }, [authHeaders, watchlistId]);

  useEffect(() => {
    void load();
  }, [load]);

  function selectTab(key: string) {
    if (!isTab(key)) return;
    setTab(key);
    const query = new URLSearchParams(searchParams.toString());
    query.set('tab', key);
    router.replace(`/admin/external-watchlist/${watchlistId}?${query.toString()}`, { scroll: false });
  }

  async function act(label: string, run: () => Promise<{ ok: boolean; status: number; payload: Record<string, unknown> }>, success: string) {
    setBusy(true);
    setActionError(null);
    setActionMessage(null);
    try {
      const result = await run();
      if (!result.ok) {
        setActionError(apiErrorMessage(result.status, result.payload, `${label} failed`));
        return false;
      }
      setActionMessage(success);
      await load();
      return true;
    } catch {
      setActionError(`${label} failed.`);
      return false;
    } finally {
      setBusy(false);
    }
  }

  if (loading) {
    return <main className="adminConsole"><p className="muted">Loading…</p></main>;
  }
  if (notFound) {
    return (
      <main className="adminConsole">
        <Link href="/admin/external-watchlist" prefetch={false} className="ewlBack">← External Watchlist</Link>
        <p className="muted">This protocol is not on the External Watchlist.</p>
      </main>
    );
  }
  if (!detail) {
    return (
      <main className="adminConsole">
        <Link href="/admin/external-watchlist" prefetch={false} className="ewlBack">← External Watchlist</Link>
        <p className="statusLine" style={{ color: 'var(--danger-fg)' }}>{error ?? 'This protocol could not be loaded.'}</p>
      </main>
    );
  }

  const { watchlist, overview } = detail;
  const paused = watchlist.monitoring_enabled === false;

  return (
    <main className="adminConsole ewlPage" data-testid="external-watchlist-detail">
      <Link href="/admin/external-watchlist" prefetch={false} className="ewlBack">← External Watchlist</Link>
      <div className="ewlPageHeader">
        <div>
          <div className="ewlTitleRow">
            <h1 className="ewlTitle">{watchlist.name}</h1>
            <span className="ewlBadge" data-testid="public-infrastructure-badge">{PUBLIC_INFRASTRUCTURE_BADGE}</span>
            <span title={overview.status_reason_label ?? undefined}>
              <StatusPill label={statusLabel(overview.status)} variant={statusVariant(overview.status)} />
            </span>
          </div>
          <p className="ewlSubtitle">
            {networkLabels(watchlist.networks)}
            {watchlist.website_url ? (
              <>
                {' · '}
                <a href={watchlist.website_url} target="_blank" rel="noopener noreferrer nofollow">{watchlist.website_url}</a>
              </>
            ) : null}
            {overview.status_reason_label ? <> · {overview.status_reason_label}</> : null}
          </p>
        </div>
        <div className="ewlHeaderActions">
          <button type="button" className="btn btn-secondary" disabled={!csrfReady || busy}
            onClick={() => void act(paused ? 'Resume' : 'Pause',
              () => sendJson(watchlistUrl(watchlistId), 'PATCH', { monitoring_enabled: paused }, authHeaders, refreshCsrfToken),
              paused ? 'Monitoring resumed.' : 'Monitoring paused. No public data is read while paused.')}>
            {paused ? 'Resume monitoring' : 'Pause monitoring'}
          </button>
          <span className="ewlInlineControl">
            <select aria-label="Backfill window" value={String(backfillDays)} onChange={(event) => setBackfillDays(Number(event.target.value))}>
              <option value="7">7 days</option>
              <option value="14">14 days</option>
              <option value="30">30 days</option>
            </select>
            <button type="button" className="btn btn-secondary" disabled={!csrfReady || busy || detail.targets.length === 0}
              onClick={() => void act('Backfill',
                () => sendJson(watchlistUrl(watchlistId, '/backfill'), 'POST', { days: backfillDays }, authHeaders, refreshCsrfToken),
                'Historical backfill queued. The worker scans history in the background.')}>
              Run historical backfill
            </button>
          </span>
          {detail.conversion ? null : (
            <button type="button" className="btn btn-primary" disabled={!csrfReady || busy} data-testid="convert-to-pilot"
              onClick={() => setConverting(true)}>
              Convert to Pilot Workspace
            </button>
          )}
        </div>
      </div>

      <div className="ewlNotice ewlNoticeProminent" role="note" data-testid="external-watchlist-detail-notice">
        {detail.notices?.detail ?? DETAIL_NOTICE}
      </div>

      {actionMessage ? <p className="statusLine ewlSuccess" role="status">{actionMessage}</p> : null}
      {actionError ? <p className="statusLine" style={{ color: 'var(--danger-fg)' }} role="alert">{actionError}</p> : null}

      <TabStrip tabs={TABS.map((item) => ({ key: item.key, label: item.label }))} active={tab} onChange={selectTab} />

      <div className="ewlTabBody">
        {tab === 'overview' ? <OverviewTab detail={detail} /> : null}
        {tab === 'targets' ? (
          <TargetsTab watchlistId={watchlistId} detail={detail} feature={feature} onChanged={load}
            onInspectEvents={(targetId) => {
              setEventsTargetId(targetId);
              selectTab('events');
            }} />
        ) : null}
        {tab === 'events' ? (
          <EventsTab key={eventsTargetId || 'all'} watchlistId={watchlistId} targets={detail.targets} initialTargetId={eventsTargetId} />
        ) : null}
        {tab === 'findings' ? <FindingsTab watchlistId={watchlistId} /> : null}
        {tab === 'evidence' ? <EvidenceTab watchlistId={watchlistId} /> : null}
      </div>

      <section className="ewlDangerZone">
        {confirmDelete ? (
          <span className="ewlInlineControl">
            <span className="muted">Stop watching {watchlist.name}? Monitoring stops; history, findings and evidence are kept.</span>
            <button type="button" className="btn btn-danger" disabled={!csrfReady || busy}
              onClick={async () => {
                const done = await act('Stop watching',
                  () => sendJson(watchlistUrl(watchlistId), 'DELETE', {}, authHeaders, refreshCsrfToken), 'Removed.');
                if (done) router.push('/admin/external-watchlist');
              }}>
              Stop watching
            </button>
            <button type="button" className="btn btn-ghost" onClick={() => setConfirmDelete(false)}>Cancel</button>
          </span>
        ) : (
          <button type="button" className="btn btn-ghost" onClick={() => setConfirmDelete(true)}>Stop watching this protocol…</button>
        )}
      </section>

      {converting ? (
        <ConvertToPilotDialog
          watchlistId={watchlistId}
          protocolName={watchlist.name}
          targets={detail.targets}
          evaluationDays={feature.pilot_evaluation_days ?? 30}
          onClose={() => setConverting(false)}
          onConverted={() => void load()}
        />
      ) : null}
    </main>
  );
}

function OverviewTab({ detail }: { detail: WatchlistDetail }) {
  const { overview } = detail;
  const now = new Date();
  const backfill = overview.backfill;
  const rpcSummary = overview.rpc_health.length
    ? overview.rpc_health.map((item) => `${item.network_label}: ${item.state.replace('_', ' ')}`).join(' · ')
    : 'No monitored networks';
  const lastBlocks = overview.last_block_processed.length
    ? overview.last_block_processed
        .map((item) => `${item.network.short_label} ${item.block != null ? `#${item.block.toLocaleString('en-US')}` : '— not yet'}`)
        .join(' · ')
    : '—';

  return (
    <section data-testid="overview-tab">
      <div className="ewlMetrics">
        <MetricTile label="Monitoring Status" value={statusLabel(overview.status)}
          meta={overview.status_reason_label ?? (overview.status === 'live' ? 'All enabled targets polled recently' : undefined)} />
        <MetricTile label="Contracts monitored" value={overview.contracts_monitored} />
        <MetricTile label="Wallets monitored" value={overview.wallets_monitored} />
        <MetricTile label="Events — 24h" value={overview.events_24h} meta={`${overview.events_total} observed in total`} />
        <MetricTile label="Findings — 30d" value={overview.findings_30d} meta={`${overview.findings_new} new`} />
        <MetricTile label="Last block processed" value={lastBlocks} />
        <MetricTile label="RPC health" value={rpcSummary} meta="From recorded poll outcomes" />
        <MetricTile label="Last successful poll" value={formatRelativeTime(overview.last_successful_poll_at, now)}
          meta={overview.last_successful_poll_at ? formatTimestamp(overview.last_successful_poll_at) : 'No successful poll yet'} />
      </div>

      <div className="ewlSplit">
        <article className="dataCard" data-testid="backfill-state">
          <p className="sectionEyebrow">Backfill</p>
          {backfill ? (
            <>
              <p className="ewlBackfillLine">
                <strong>{backfillStatusLabel(backfill.status)}</strong>
                <span className="muted"> · {backfillProgressLabel(backfill)}</span>
              </p>
              <div className="ewlProgress ewlProgressLarge" role="progressbar" aria-valuemin={0} aria-valuemax={100}
                aria-valuenow={clampPercent(backfill.percent)} aria-label="Historical backfill progress">
                <span className="ewlProgressFill" style={{ width: `${clampPercent(backfill.percent)}%` }} />
              </div>
              <p className="muted ewlSmall">{clampPercent(backfill.percent).toFixed(0)}% of the requested block range scanned.</p>
            </>
          ) : (
            <p className="muted">No historical backfill requested. Monitoring covers blocks from when the target was added.</p>
          )}
        </article>

        <article className="dataCard">
          <p className="sectionEyebrow">Monitoring facts</p>
          <dl className="ewlFacts">
            <dt>Worker heartbeat</dt>
            <dd>{overview.worker_heartbeat_at ? formatRelativeTime(overview.worker_heartbeat_at, now) : 'Not reported'}</dd>
            <dt>Last successful poll</dt>
            <dd>{formatRelativeTime(overview.last_successful_poll_at, now)}</dd>
            <dt>Last observed event</dt>
            <dd>{overview.recent_activity[0]?.observed_at ? formatRelativeTime(overview.recent_activity[0].observed_at, now) : 'No events observed yet'}</dd>
          </dl>
          <p className="muted ewlSmall">
            Heartbeat, poll and telemetry are separate facts: a live worker is not proof that the chain was read, and a
            successful poll with no events is not a statement that nothing needs review.
          </p>
        </article>
      </div>

      {detail.conversion ? (
        <article className="dataCard ewlConversionCard" data-testid="conversion-summary">
          <p className="sectionEyebrow">Pilot workspace</p>
          <p>
            A Pilot workspace was created from this protocol on {formatTimestamp(detail.conversion.converted_at)} with a{' '}
            {detail.conversion.evaluation_days}-day evaluation
            {detail.conversion.evaluation_expires_at ? ` (ends ${formatTimestamp(detail.conversion.evaluation_expires_at).slice(0, 10)})` : ''}.{' '}
            {detail.conversion.copied_targets.length} target(s) were copied as inactive drafts.
          </p>
          <p className="muted ewlSmall">
            Customer authorization is not recorded here. It is established only when the customer accepts the workspace
            invitation and enables monitoring in their own workspace.
          </p>
        </article>
      ) : null}

      <article className="dataCard">
        <p className="sectionEyebrow">Recent activity</p>
        {overview.recent_activity.length === 0 ? (
          <p className="muted" data-testid="recent-activity-empty">
            No events observed yet. This is not a statement that nothing changed — only that no matching public event has
            been read for the monitored targets.
          </p>
        ) : (
          <ul className="ewlActivity">
            {overview.recent_activity.map((item) => (
              <li key={`${item.tx_hash}-${item.block_number}-${item.event_name}`}>
                <span className="ewlActivityName">{item.event_name}</span>
                <span className="muted">{EVENT_CATEGORY_LABELS[item.event_category] ?? item.event_category}</span>
                <span className="muted">block #{item.block_number.toLocaleString('en-US')}</span>
                <span className="muted" title={formatTimestamp(item.observed_at)}>{formatRelativeTime(item.observed_at, now)}</span>
                {item.tx_explorer_url ? (
                  <a href={item.tx_explorer_url} target="_blank" rel="noopener noreferrer nofollow" className="ewlMono">
                    {item.tx_hash.slice(0, 10)}…
                  </a>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </article>
    </section>
  );
}
