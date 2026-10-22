'use client';

import Link from 'next/link';
import { Suspense, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';

import { Select, StatusPill, TableShell } from './components/ui-primitives';
import { followDecodaRefusal } from './decoda-identity-shared';
import { usePilotAuth } from './pilot-auth-context';
import {
  confidenceText,
  confidenceVariant,
  exactTime,
  getTriageDisplayState,
  recommendationLabel,
  relativeTime,
  severityLabel,
  severityVariant,
  shortHash,
  statusLabel,
  statusVariant,
  triageModeLabel,
  triageModeVariant,
  triageSummaryText,
  type Screen6Alert,
  type Screen6Summary,
  type TriageClusterView,
} from './alerts-triage-presentation';

const API_PROXY_BASE = '/api';
const PAGE_SIZE = 25;

const SEVERITY_TABS: Array<{ key: string; label: string; countKey: keyof Screen6Summary['severity_totals'] }> = [
  { key: '', label: 'All', countKey: 'all' },
  { key: 'critical', label: 'Critical', countKey: 'critical' },
  { key: 'high', label: 'High', countKey: 'high' },
  { key: 'medium', label: 'Medium', countKey: 'medium' },
  { key: 'low', label: 'Low', countKey: 'low' },
];

const TABLE_HEADERS = ['Severity', 'Alert Title', 'Asset', 'Status', 'Time', 'Cluster'];

/* Presentation-only tone for the severity rail and drawer accents. It reads
   the canonical severity string and fails closed to `unknown` (neutral), so
   an unrecognised value is never painted as a known severity. */
type SeverityTone = 'critical' | 'high' | 'medium' | 'low' | 'unknown';
function severityTone(severity: string | null | undefined): SeverityTone {
  const s = (severity || '').toLowerCase();
  return s === 'critical' || s === 'high' || s === 'medium' || s === 'low' ? s : 'unknown';
}

export default function AlertsScreen() {
  // useSearchParams requires a Suspense boundary; the page owns the <h1>.
  return (
    <Suspense fallback={<div className="muted alertsSuspenseFallback">Loading alerts…</div>}>
      <AlertsScreenInner />
    </Suspense>
  );
}

function AlertsScreenInner() {
  const { authHeaders, signOut } = usePilotAuth();
  const router = useRouter();
  const searchParams = useSearchParams();
  const apiUrl = API_PROXY_BASE;

  // URL is the single source of truth for tab + filters + page, so refresh
  // restores them and Back/Forward navigates between them.
  const severity = searchParams.get('severity') ?? '';
  const assetId = searchParams.get('asset') ?? '';
  const statusValue = searchParams.get('status') ?? '';
  const clusterId = searchParams.get('cluster') ?? '';
  const searchQuery = searchParams.get('q') ?? '';
  const page = Math.max(1, Number.parseInt(searchParams.get('page') ?? '1', 10) || 1);

  const [summary, setSummary] = useState<Screen6Summary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [searchInput, setSearchInput] = useState(searchQuery);
  const [runningTriage, setRunningTriage] = useState(false);
  const [triageMessage, setTriageMessage] = useState('');
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [selectedAlertId, setSelectedAlertId] = useState<string | null>(null);

  const hasFilters = !!(severity || assetId || statusValue || clusterId || searchQuery);

  const setUrl = useCallback((next: Record<string, string | null>, opts: { resetPage?: boolean } = {}) => {
    const params = new URLSearchParams(searchParams.toString());
    for (const [k, v] of Object.entries(next)) {
      if (v === null || v === '') params.delete(k);
      else params.set(k, v);
    }
    // Changing any filter resets pagination to page 1 (requirement 5).
    if (opts.resetPage) params.delete('page');
    // Navigation only — reading Screen 6 never writes to the database.
    router.replace(`/alerts?${params.toString()}`, { scroll: false });
  }, [router, searchParams]);

  const loadSummary = useCallback(async () => {
    setError('');
    // No token check here: the session is the HttpOnly cookie, attached by the
    // server. A missing or expired session is the 401 handled below.
    const headers = authHeaders();
    try {
      const params = new URLSearchParams();
      if (severity) params.set('severity', severity);
      if (assetId && assetId !== 'unassigned') params.set('asset_id', assetId);
      else if (assetId === 'unassigned') params.set('asset_id', 'unassigned');
      if (statusValue) params.set('status_value', statusValue);
      if (clusterId) params.set('cluster_id', clusterId);
      if (searchQuery) params.set('search', searchQuery);
      params.set('limit', String(PAGE_SIZE));
      params.set('offset', String((page - 1) * PAGE_SIZE));
      // GET only — opening/refreshing/filtering Screen 6 never writes.
      const res = await fetch(`${apiUrl}/alerts/triage/summary?${params.toString()}`, {
        headers: { ...headers }, cache: 'no-store',
      });
      if (res.status === 401 || res.status === 403) {
        if (await followDecodaRefusal(res)) return;
        await signOut();
        setError('Your session is missing or expired. Please sign in again.');
        return;
      }
      if (!res.ok) {
        setError('Unable to load the active alerts right now.');
        return;
      }
      setSummary((await res.json()) as Screen6Summary);
    } catch {
      setError('The active alerts view is temporarily unavailable.');
    } finally {
      setLoading(false);
    }
  }, [apiUrl, authHeaders, signOut, severity, assetId, statusValue, clusterId, searchQuery, page]);

  useEffect(() => {
    setLoading(true);
    void loadSummary();
  }, [loadSummary]);

  // Debounce the search box into the URL query.
  useEffect(() => { setSearchInput(searchQuery); }, [searchQuery]);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const onSearchChange = useCallback((value: string) => {
    setSearchInput(value);
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      setUrl({ q: value || null }, { resetPage: true });
    }, 350);
  }, [setUrl]);

  const runTriage = useCallback(async () => {
    setRunningTriage(true);
    setTriageMessage('');
    try {
      const res = await fetch(`${apiUrl}/alerts/triage/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        cache: 'no-store',
      });
      const payload = (await res.json().catch(() => ({}))) as { status?: string; detail?: string; triage_run?: { clusters_total?: number } };
      if (res.status === 403) {
        setTriageMessage('You do not have permission to run triage.');
      } else if (res.ok) {
        if (payload.status === 'already_running') {
          setTriageMessage('A triage run is already in progress.');
        } else {
          const n = payload.triage_run?.clusters_total ?? 0;
          setTriageMessage(`Triage complete. ${n} active cluster${n === 1 ? '' : 's'}.`);
        }
        await loadSummary();
      } else {
        setTriageMessage(payload.detail || 'Triage run failed. Check logs for details.');
      }
    } catch {
      setTriageMessage('Triage run failed — network error.');
    } finally {
      setRunningTriage(false);
    }
  }, [apiUrl, authHeaders, loadSummary]);

  const totals = summary?.severity_totals ?? { all: 0, critical: 0, high: 0, medium: 0, low: 0 };
  // A count is only shown when the read model actually returned it. While the
  // summary is loading, failed, or came back without totals the badge reads
  // "—" — an unavailable count must never read as "0 alerts".
  const countsKnown = !!summary?.severity_totals && !error;
  const alerts = summary?.alerts ?? [];
  const pagination = summary?.pagination;
  const triage = summary?.triage ?? null;

  const selectedAlert = useMemo(
    () => alerts.find((a) => a.id === selectedAlertId) ?? null,
    [alerts, selectedAlertId],
  );

  // The cluster the queue is filtered to, when the triage preview names it.
  const activeCluster = useMemo(
    () => (clusterId ? (triage?.clusters_preview ?? []).find((c) => c.id === clusterId) ?? null : null),
    [clusterId, triage],
  );

  const clearFilters = useCallback(() => {
    router.replace('/alerts', { scroll: false });
    setSearchInput('');
  }, [router]);

  const assetOptions = useMemo(() => ([
    { value: '', label: 'All assets' },
    ...(summary?.filters?.assets ?? []).map((a) => ({ value: a.value, label: a.label })),
  ]), [summary]);

  const statusOptions = useMemo(() => ([
    { value: '', label: 'All statuses' },
    ...(summary?.filters?.statuses ?? []).map((s) => ({ value: s, label: statusLabel(s) })),
  ]), [summary]);

  return (
    <div className="alertsScreen">
      {/* Two-column split (~75% alerts, ~25% triage agent) on wide desktops;
          stacks to one column below 1360px so the table keeps the full
          main-content width. Sizing lives in .alertsSplit (styles.css). */}
      <div className="alertsSplit">
        {/* ── Main panel ─────────────────────────────────────────────── */}
        <main className="alertsMain">
          <div className="alertsQueue">
            {/* Severity tabs with real counts (from the same read model as the rows). */}
            <div className="alertsQueueHead">
              <div className="buttonRow sharedTabStrip alertsSevTabs" role="tablist" aria-label="Severity">
                {SEVERITY_TABS.map((tab) => (
                  <button
                    key={tab.key || 'all'}
                    type="button"
                    role="tab"
                    aria-selected={severity === tab.key}
                    className={severity === tab.key ? 'alertsSevTab activeTab' : 'alertsSevTab'}
                    data-sev={tab.key || 'all'}
                    onClick={() => setUrl({ severity: tab.key || null }, { resetPage: true })}
                    data-testid={`severity-tab-${tab.key || 'all'}`}
                  >
                    {tab.key ? <span className="alertsSevDot" aria-hidden="true" /> : null}
                    {tab.label}
                    <span
                      className="alertsTabCount"
                      data-hot={countsKnown && tab.countKey === 'critical' && totals.critical > 0 ? 'true' : undefined}
                      aria-label={countsKnown ? `${totals[tab.countKey]} alerts` : 'Count unavailable'}
                    >
                      {countsKnown ? totals[tab.countKey] : '—'}
                    </span>
                  </button>
                ))}
              </div>
            </div>

            {/* Filter toolbar: search and filters share one control row. */}
            <div className="buttonRow alertsToolbar">
              <div className="alertsSearch">
                <span className="alertsGlyph alertsGlyph--search" aria-hidden="true" />
                <input
                  placeholder="Search title, asset, tx, contract, actor, cluster…"
                  value={searchInput}
                  onChange={(e) => onSearchChange(e.target.value)}
                  aria-label="Search alerts"
                  data-testid="alerts-search"
                />
              </div>
              <div className="alertsFilter alertsFilter--asset">
                <Select
                  value={assetId}
                  onValueChange={(v) => setUrl({ asset: v || null }, { resetPage: true })}
                  options={assetOptions}
                  ariaLabel="Filter by asset"
                  testId="asset-filter"
                />
              </div>
              <div className="alertsFilter alertsFilter--status">
                <Select
                  value={statusValue}
                  onValueChange={(v) => setUrl({ status: v || null }, { resetPage: true })}
                  options={statusOptions}
                  ariaLabel="Filter by status"
                  testId="status-filter"
                />
              </div>
              {hasFilters ? (
                <button type="button" className="btn btn-ghost alertsClearFilters" onClick={clearFilters} data-testid="clear-filters">
                  Clear filters
                </button>
              ) : null}
            </div>

            {clusterId ? (
              <div className="statusLine alertsClusterFilter" role="status">
                <span className="alertsGlyph alertsGlyph--cluster" aria-hidden="true" />
                <span className="alertsClusterFilterText">
                  <span>Filtered to a single cluster.</span>
                  {activeCluster?.title ? <strong className="alertsClusterFilterName">{activeCluster.title}</strong> : null}
                </span>
                <button type="button" className="btn btn-ghost alertsClusterFilterReset" onClick={() => setUrl({ cluster: null }, { resetPage: true })}>
                  Show all alerts
                </button>
              </div>
            ) : null}

            <AlertsTableRegion
              loading={loading}
              error={error}
              summary={summary}
              alerts={alerts}
              hasFilters={hasFilters}
              selectedAlertId={selectedAlertId}
              onSelect={setSelectedAlertId}
              onRetry={() => { setLoading(true); void loadSummary(); }}
              onClearFilters={clearFilters}
            />

            {/* Pagination — accurate range + total, backend-paginated. */}
            {pagination && pagination.total > 0 ? (
              <div className="alertsPager">
                <span className="muted alertsPagerRange" data-testid="pagination-range">
                  Showing {pagination.showing_from}–{pagination.showing_to} of {pagination.total}
                </span>
                <div className="buttonRow alertsPagerButtons">
                  <button
                    type="button"
                    className="btn btn-secondary"
                    disabled={page <= 1}
                    onClick={() => setUrl({ page: String(page - 1) })}
                    data-testid="page-prev"
                  >
                    Previous
                  </button>
                  <button
                    type="button"
                    className="btn btn-secondary"
                    disabled={!pagination.has_more}
                    onClick={() => setUrl({ page: String(page + 1) })}
                    data-testid="page-next"
                  >
                    Next
                  </button>
                </div>
              </div>
            ) : null}
          </div>
        </main>

        {/* ── AI Alert Triage Agent panel ────────────────────────────── */}
        <TriagePanel
          triage={triage}
          loading={loading && !summary}
          runningTriage={runningTriage}
          triageMessage={triageMessage}
          onRunTriage={runTriage}
          onReviewClusters={() => setDrawerOpen(true)}
        />
      </div>

      {drawerOpen ? (
        <ClusterReviewDrawer
          clusters={triage?.clusters_preview ?? []}
          activeClusterId={clusterId}
          onSelect={(id) => { setUrl({ cluster: id }, { resetPage: true }); setDrawerOpen(false); }}
          onClose={() => setDrawerOpen(false)}
        />
      ) : null}

      {selectedAlert ? (
        <AlertDetailDrawer
          alert={selectedAlert}
          apiUrl={apiUrl}
          authHeaders={authHeaders}
          onClose={() => setSelectedAlertId(null)}
          onViewCluster={(id) => { setUrl({ cluster: id }, { resetPage: true }); setSelectedAlertId(null); }}
          onViewAsset={(value) => { setUrl({ asset: value }, { resetPage: true }); setSelectedAlertId(null); }}
        />
      ) : null}
    </div>
  );
}

/* ── Severity pill ──────────────────────────────────────────────────────── */
// The canonical label + variant, wrapped so the screen can keep the severity
// hue scale (low = informational blue) without a green "low" reading as healthy.
function SeverityPill({ severity }: { severity: string | null | undefined }) {
  return (
    <span className="alertsSev" data-sev={severityTone(severity)}>
      <StatusPill label={severityLabel(severity)} variant={severityVariant(severity)} />
    </span>
  );
}

/* ── Table region with all required states ──────────────────────────────── */
function AlertsTableRegion({
  loading, error, summary, alerts, hasFilters, selectedAlertId, onSelect, onRetry, onClearFilters,
}: {
  loading: boolean;
  error: string;
  summary: Screen6Summary | null;
  alerts: Screen6Alert[];
  hasFilters: boolean;
  selectedAlertId: string | null;
  onSelect: (id: string) => void;
  onRetry: () => void;
  onClearFilters: () => void;
}) {
  if (loading && !summary) {
    return (
      <div className="emptyStatePanel sharedEmptyStateBlocker alertsState alertsStateLoading" data-testid="alerts-loading" aria-busy="true">
        <h4>Loading active alerts…</h4>
      </div>
    );
  }
  if (error) {
    return (
      <div className="emptyStatePanel sharedEmptyStateBlocker alertsState alertsStateError" data-testid="alerts-error" role="alert">
        <h4>Couldn’t load alerts</h4>
        <p className="muted">{error}</p>
        <button type="button" className="btn btn-secondary alertsStateAction" onClick={onRetry}>Retry</button>
      </div>
    );
  }
  if (alerts.length === 0) {
    // Distinguish: empty workspace vs no active alerts vs no results for filters.
    if (hasFilters) {
      return (
        <div className="emptyStatePanel sharedEmptyStateBlocker alertsState alertsStateNoMatch" data-testid="alerts-empty-filters">
          <h4>No alerts match the current filters</h4>
          <p className="muted">Adjust or clear the filters above to see more results.</p>
          <button type="button" className="btn btn-secondary alertsStateAction" onClick={onClearFilters}>Clear all filters</button>
        </div>
      );
    }
    if ((summary?.active_alert_count ?? 0) === 0) {
      return (
        <div className="emptyStatePanel sharedEmptyStateBlocker alertsState alertsStateEmpty" data-testid="alerts-empty">
          <h4>No active alerts</h4>
          <p className="muted">There are no active alerts in this workspace. New alerts appear here as telemetry and detections arrive.</p>
        </div>
      );
    }
  }
  return (
    <TableShell headers={TABLE_HEADERS} compact className="alertsTableShell">
      {alerts.map((alert) => {
        const isSelected = alert.id === selectedAlertId;
        return (
          // Rows never animate: they re-render on every refresh and filter.
          // The severity rail is a static inset edge keyed off data-sev.
          <tr
            key={alert.id}
            className="alertsRow"
            data-sev={severityTone(alert.severity)}
            data-selected={isSelected ? 'true' : undefined}
            onClick={() => onSelect(alert.id)}
            data-testid="alert-row"
          >
            <td className="alertsSevCell"><SeverityPill severity={alert.severity} /></td>
            {/* Title wraps within its fixed column (see .alertsTableShell) so long
                titles never force a horizontal scrollbar. The title is a real
                button so the detail drawer is reachable from the keyboard. */}
            <td className="alertsTitleCell">
              <button
                type="button"
                className="alertsRowTitle"
                aria-haspopup="dialog"
                onClick={(e) => { e.stopPropagation(); onSelect(alert.id); }}
              >
                {alert.title}
              </button>
              {alert.detection_family_label ? (
                <div className="alertsRowMeta">{alert.detection_family_label}</div>
              ) : null}
              {alert.evidence_label ? (
                <div className="alertsRowMeta" data-testid="alert-evidence-label">
                  <StatusPill label={alert.evidence_label} variant="info" />
                </div>
              ) : null}
            </td>
            <td className="alertsAssetCell">
              <span className={alert.asset_known ? 'alertsAssetName' : 'alertsAssetName alertsAssetUnknown'}>{alert.asset_name}</span>
              {alert.tx_hash ? (
                <div className="alertsHash" title={alert.tx_hash}>
                  {shortHash(alert.tx_hash)}
                </div>
              ) : null}
            </td>
            <td className="alertsStatusCell"><StatusPill label={statusLabel(alert.status)} variant={statusVariant(alert.status)} /></td>
            <td className="alertsTimeCell" title={exactTime(alert.occurred_at)}>
              {relativeTime(alert.occurred_at)}
            </td>
            <td className="alertsClusterCell">
              {alert.cluster ? (
                <button
                  type="button"
                  className="btn btn-ghost alertsClusterChip"
                  // Override the .btn nowrap so a long cluster title wraps inside
                  // the fixed Cluster column instead of overflowing it.
                  style={{ whiteSpace: 'normal', overflowWrap: 'anywhere' }}
                  title={alert.cluster.title ?? undefined}
                  onClick={(e) => { e.stopPropagation(); onSelect(alert.id); }}
                >
                  {truncate(alert.cluster.title ?? 'Cluster', 22)}
                </button>
              ) : (
                <span className="muted alertsUnclustered" data-testid="unclustered">Unclustered</span>
              )}
            </td>
          </tr>
        );
      })}
    </TableShell>
  );
}

/* ── AI Alert Triage Agent panel ────────────────────────────────────────── */
function TriagePanel({
  triage, loading, runningTriage, triageMessage, onRunTriage, onReviewClusters,
}: {
  triage: Screen6Summary['triage'] | null;
  loading: boolean;
  runningTriage: boolean;
  triageMessage: string;
  onRunTriage: () => void;
  onReviewClusters: () => void;
}) {
  const display = getTriageDisplayState({ triage, mutationInFlight: runningTriage });
  const top = triage?.top_recommendation ?? null;

  return (
    <aside className="dataCard sharedSurfaceCard alertsAgent" aria-label="AI Alert Triage Agent" data-testid="triage-panel">
      <div className="alertsAgentHead">
        <span className="alertsAgentMark" aria-hidden="true"><span className="alertsGlyph alertsGlyph--agent" /></span>
        <div className="alertsAgentHeading">
          <p className="eyebrow alertsAgentEyebrow">Autonomous Agent</p>
          <h3 className="alertsAgentTitle">AI Alert Triage Agent</h3>
        </div>
      </div>

      {loading ? (
        <p className="muted alertsAgentLoading">Loading triage state…</p>
      ) : (
        <>
          {/* Summary line + operational facts. */}
          <div className="alertsAgentPills">
            <StatusPill label={triageModeLabel(triage?.mode)} variant={triageModeVariant(triage?.mode)} />
            <StatusPill label={display.statusLabel} variant={display.statusVariant} />
          </div>
          <p className="alertsAgentSummary" data-testid="triage-summary">
            {triageSummaryText(triage)}
          </p>
          <dl className="alertsAgentFacts">
            <div className="alertsAgentFact">
              <dt>Last successful triage</dt>
              <dd>
                <span title={exactTime(triage?.last_success_at)}>
                  {triage?.last_success_at ? relativeTime(triage.last_success_at) : 'never'}
                </span>
              </dd>
            </div>
            <div className="alertsAgentFact" data-testid="unclustered-count">
              <dt>Unclustered active alerts</dt>
              <dd>
                {triage?.unclustered_count === null || triage?.unclustered_count === undefined ? 'Not calculated' : triage.unclustered_count}
              </dd>
            </div>
            {triage && !triage.ai_available ? (
              <div className="alertsAgentFact" title="No live AI provider is configured; clustering and recommendations remain deterministic.">
                <dt>AI provider</dt>
                <dd>Unavailable — deterministic rules only</dd>
              </div>
            ) : null}
          </dl>
          {triage?.state === 'degraded' && triage.last_run?.error_code ? (
            <div className="alertsAgentNotice" data-testid="triage-degraded">
              <span className="alertsGlyph alertsGlyph--warn" aria-hidden="true" />
              <span>Last run failed ({triage.last_run.error_code}); showing the previous successful result.</span>
            </div>
          ) : null}

          {/* Top recommendation. */}
          {top ? (
            <div className="alertsAgentRec" data-sev={severityTone(top.derived_severity)} data-testid="top-recommendation">
              <p className="sectionEyebrow alertsAgentRecEyebrow">Top recommendation</p>
              <div className="alertsAgentRecPills">
                <SeverityPill severity={top.derived_severity} />
                <StatusPill label={recommendationLabel(top.recommendation_category)} variant="info" />
              </div>
              <p className="alertsAgentRecTitle">{top.title}</p>
              <dl className="alertsAgentRecFacts">
                <div><dt>Affected asset</dt><dd>{top.primary_asset_name || 'Unassigned asset'}</dd></div>
                <div><dt>Cluster size</dt><dd>{top.member_count} alert{top.member_count === 1 ? '' : 's'} in this cluster</dd></div>
                <div>
                  <dt>Confidence</dt>
                  <dd><span data-testid="top-confidence">{confidenceText(top.confidence, top.confidence_band)}</span></dd>
                </div>
              </dl>
              {/* AI summary is rendered as plain text via JSX interpolation — never as raw HTML. */}
              {top.recommendation_summary ? (
                <p className="alertsAgentRecSummary">{top.recommendation_summary}</p>
              ) : null}
              {top.recommendation_source === 'ai_assisted' ? (
                <p className="muted alertsAgentProvenance">AI-generated from stored cluster evidence.</p>
              ) : (
                <p className="muted alertsAgentProvenance">Generated from stored cluster evidence.</p>
              )}
              {/* Reasoning chips. */}
              {top.reason_labels && top.reason_labels.length > 0 ? (
                <div className="alertsReasonChips" data-testid="triage-reasons">
                  {top.reason_labels.slice(0, 6).map((r) => (
                    <span key={r} className="alertsReasonChip">{r}</span>
                  ))}
                </div>
              ) : null}
            </div>
          ) : triage?.schema_ready ? (
            <p className="muted alertsAgentEmpty" data-testid="no-clusters">
              {triage.state === 'not_run'
                ? 'No triage has run yet. Run triage to group related alerts into clusters.'
                : 'No related-alert clusters were found in the current active set.'}
            </p>
          ) : (
            <p className="muted alertsAgentEmpty" data-testid="triage-provisioning">
              Alert triage storage is provisioning.
            </p>
          )}

          {/* Actions. */}
          <div className="buttonRow alertsAgentActions">
            <button
              type="button"
              className="btn btn-secondary"
              onClick={onReviewClusters}
              disabled={(triage?.cluster_count ?? 0) === 0}
              data-testid="review-clusters"
            >
              Review Clusters{triage?.cluster_count ? ` (${triage.cluster_count})` : ''}
            </button>
            <button
              type="button"
              className="btn btn-primary"
              onClick={onRunTriage}
              disabled={display.runDisabled}
              title={display.hint}
              data-testid="run-triage"
            >
              {display.runBusy ? 'Running…' : display.runLabel}
            </button>
          </div>
          {display.hint && !runningTriage ? (
            <p className="muted alertsAgentHint">{display.hint}</p>
          ) : null}
          {triageMessage ? (
            <p className="statusLine alertsAgentMessage" data-testid="triage-message">{triageMessage}</p>
          ) : null}
        </>
      )}
    </aside>
  );
}

/* ── Drawer behaviour shared by both overlays ───────────────────────────── */
// Escape closes, focus moves into the drawer panel on open (so Tab continues
// inside it) and returns to the control that opened the drawer when it closes. The latest onClose is read
// through a ref so a parent re-render (polling) never re-steals focus.
function useDrawerDismiss(onClose: () => void) {
  const panelRef = useRef<HTMLElement | null>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  useEffect(() => {
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    panelRef.current?.focus({ preventScroll: true });
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') onCloseRef.current(); };
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('keydown', onKey);
      if (opener && opener.isConnected) opener.focus({ preventScroll: true });
    };
  }, []);
  return panelRef;
}

/* ── Cluster review drawer ──────────────────────────────────────────────── */
function ClusterReviewDrawer({
  clusters, activeClusterId, onSelect, onClose,
}: {
  clusters: TriageClusterView[];
  activeClusterId: string;
  onSelect: (id: string) => void;
  onClose: () => void;
}) {
  const panelRef = useDrawerDismiss(onClose);
  return (
    <div className="alertsDrawerScrim" onClick={onClose} role="dialog" aria-modal="true" aria-label="Cluster review" data-testid="cluster-drawer">
      <aside ref={panelRef} tabIndex={-1} className="alertsDrawer" onClick={(e) => e.stopPropagation()}>
        <header className="alertsDrawerHead">
          <div className="alertsDrawerHeadRow">
            <p className="eyebrow alertsDrawerEyebrow">Alert Triage Agent</p>
            <button type="button" className="btn btn-ghost alertsDrawerClose" onClick={onClose} aria-label="Close">
              <span className="alertsGlyph alertsGlyph--close" aria-hidden="true" />
            </button>
          </div>
          <h3 className="alertsDrawerTitle">Active clusters ({clusters.length})</h3>
          <p className="muted alertsDrawerLede">
            Ordered by triage priority. Select a cluster to filter its member alerts. Response actions are not run from this screen.
          </p>
        </header>
        <div className="alertsDrawerBody">
          {clusters.length === 0 ? (
            <p className="muted alertsDrawerEmpty">No clusters to show.</p>
          ) : (
            <ol className="alertsClusterList">
              {clusters.map((c, index) => {
                const active = activeClusterId === c.id;
                return (
                  <li key={c.id}>
                    <button
                      type="button"
                      className="alertsClusterCard"
                      data-sev={severityTone(c.derived_severity)}
                      aria-current={active ? 'true' : undefined}
                      onClick={() => onSelect(c.id)}
                      data-testid="cluster-item"
                    >
                      <span className="alertsClusterRank" aria-hidden="true">{index + 1}</span>
                      <span className="alertsClusterBody">
                        <span className="alertsClusterPills">
                          <SeverityPill severity={c.derived_severity} />
                          <StatusPill label={recommendationLabel(c.recommendation_category)} variant="info" />
                          <StatusPill label={confidenceText(c.confidence, c.confidence_band)} variant={confidenceVariant(c.confidence_band)} />
                        </span>
                        <span className="alertsClusterTitle">{c.title ?? 'Untitled cluster'}</span>
                        <span className="alertsClusterMeta">
                          {c.primary_asset_name || 'Unassigned asset'} · {c.member_count} alert{c.member_count === 1 ? '' : 's'}
                        </span>
                        {c.recommendation_summary ? (
                          <span className="alertsClusterSummary">{c.recommendation_summary}</span>
                        ) : null}
                        {c.reason_labels && c.reason_labels.length > 0 ? (
                          <span className="alertsReasonChips">
                            {c.reason_labels.slice(0, 4).map((r) => (
                              <span key={r} className="alertsReasonChip">{r}</span>
                            ))}
                          </span>
                        ) : null}
                        <span className="alertsClusterCta">
                          {active ? 'Queue is filtered to this cluster' : 'Filter the queue to this cluster'}
                          <span className="alertsGlyph alertsGlyph--arrow" aria-hidden="true" />
                        </span>
                      </span>
                    </button>
                  </li>
                );
              })}
            </ol>
          )}
        </div>
      </aside>
    </div>
  );
}

/* ── Alert detail drawer (reuses the canonical escalate endpoint) ───────── */
function AlertDetailDrawer({
  alert, apiUrl, authHeaders, onClose, onViewCluster, onViewAsset,
}: {
  alert: Screen6Alert;
  apiUrl: string;
  authHeaders: () => Record<string, string>;
  onClose: () => void;
  onViewCluster: (id: string) => void;
  onViewAsset: (assetFilterValue: string) => void;
}) {
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const panelRef = useDrawerDismiss(onClose);

  const escalate = useCallback(async () => {
    if (alert.incident_id) { window.location.href = `/incidents/${alert.incident_id}`; return; }
    setBusy(true);
    setMessage('');
    try {
      const res = await fetch(`${apiUrl}/alerts/${alert.id}/escalate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ title: `Escalated alert: ${alert.title}`, summary: alert.title }),
      });
      // Navigate to the incident the backend actually created/linked (idempotent),
      // so the operator lands on the persisted /incidents/{id}, never the bare list.
      const result = (await res.json().catch(() => ({}))) as { incident_id?: string; created?: boolean; detail?: string };
      if (!res.ok) { setMessage(result.detail || 'Unable to open incident. Please retry.'); return; }
      if (result.incident_id) { window.location.href = `/incidents/${result.incident_id}`; }
    } catch {
      setMessage('Unable to open incident — network error.');
    } finally {
      setBusy(false);
    }
  }, [alert, apiUrl, authHeaders]);

  // The asset filter's values are the backend's asset ids, or 'unassigned'.
  const assetFilterValue = alert.asset_id || 'unassigned';

  return (
    <div className="alertsDrawerScrim" onClick={onClose} role="dialog" aria-modal="true" aria-label="Alert detail" data-testid="alert-detail">
      <aside ref={panelRef} tabIndex={-1} className="alertsDrawer" data-sev={severityTone(alert.severity)} onClick={(e) => e.stopPropagation()}>
        <header className="alertsDrawerHead">
          <div className="alertsDrawerHeadRow">
            <p className="eyebrow alertsDrawerEyebrow">Alert Detail</p>
            <button type="button" className="btn btn-ghost alertsDrawerClose" onClick={onClose} aria-label="Close">
              <span className="alertsGlyph alertsGlyph--close" aria-hidden="true" />
            </button>
          </div>
          <h3 className="alertsDrawerTitle">{alert.title}</h3>
          <div className="alertsDrawerPills">
            <SeverityPill severity={alert.severity} />
            <StatusPill label={statusLabel(alert.status)} variant={statusVariant(alert.status)} />
          </div>
        </header>

        <div className="alertsDrawerBody">
          <dl className="alertsFacts">
            <Field label="Asset">
              <span className={alert.asset_known ? undefined : 'alertsAssetUnknown'}>{alert.asset_name}</span>
            </Field>
            <Field label="Detection"><span>{alert.detection_family_label || '—'}</span></Field>
            <Field label="Occurred">
              <span title={exactTime(alert.occurred_at)}>{relativeTime(alert.occurred_at)}</span>
              {alert.occurred_at ? <span className="alertsFactSub">{exactTime(alert.occurred_at)}</span> : null}
            </Field>
            <Field label="Cluster">
              {alert.cluster ? (
                <span className="alertsFactCluster" title={alert.cluster.title ?? undefined}>{truncate(alert.cluster.title ?? 'Cluster', 40)}</span>
              ) : <span className="muted">Unclustered</span>}
            </Field>
          </dl>

          <section className="alertsDrawerSection" aria-labelledby="alerts-entities-heading">
            <h4 className="alertsDrawerSectionTitle" id="alerts-entities-heading">Related entities</h4>
            <dl className="alertsEntities">
              <EntityRow label="Transaction" value={alert.tx_hash} mono copyable />
              <EntityRow label="Contract" value={alert.contract_address} mono copyable />
              <EntityRow label="Actor" value={alert.actor} mono copyable />
              <EntityRow label="Chain" value={alert.chain_id} />
              <EntityRow label="Detection ID" value={alert.detection_id} mono copyable />
              <EntityRow label="Incident" value={alert.incident_id} mono emptyLabel="Not linked" />
            </dl>
          </section>

          <section className="alertsDrawerSection" aria-labelledby="alerts-investigate-heading">
            <h4 className="alertsDrawerSectionTitle" id="alerts-investigate-heading">Investigate</h4>
            <div className="alertsPivots">
              {alert.cluster ? (
                <button type="button" className="alertsPivot" onClick={() => onViewCluster(alert.cluster!.id)}>
                  <span className="alertsGlyph alertsGlyph--cluster" aria-hidden="true" />
                  <span className="alertsPivotText">
                    <span className="alertsPivotLabel">Show alerts in this cluster</span>
                    <span className="alertsPivotSub">{truncate(alert.cluster.title ?? 'Cluster', 48)}</span>
                  </span>
                  <span className="alertsGlyph alertsGlyph--arrow" aria-hidden="true" />
                </button>
              ) : null}
              <button type="button" className="alertsPivot" onClick={() => onViewAsset(assetFilterValue)}>
                <span className="alertsGlyph alertsGlyph--asset" aria-hidden="true" />
                <span className="alertsPivotText">
                  <span className="alertsPivotLabel">Show alerts for this asset</span>
                  <span className="alertsPivotSub">{alert.asset_name}</span>
                </span>
                <span className="alertsGlyph alertsGlyph--arrow" aria-hidden="true" />
              </button>
            </div>
          </section>
        </div>

        <footer className="alertsDrawerFoot">
          <div className="buttonRow alertsDrawerActions">
            {alert.incident_id ? (
              <Link href={`/incidents/${alert.incident_id}`} prefetch={false} className="btn btn-secondary">View Incident</Link>
            ) : (
              <button type="button" className="btn btn-primary" onClick={() => void escalate()} disabled={busy} data-testid="escalate-alert">
                {busy ? 'Opening…' : 'Escalate to Incident'}
              </button>
            )}
          </div>
          {message ? <p className="statusLine alertsDrawerMessage">{message}</p> : null}
        </footer>
      </aside>
    </div>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="alertsFact">
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

/* One related-entity line: the stored value or an explicit "Not recorded". */
function EntityRow({
  label, value, mono = false, copyable = false, emptyLabel = 'Not recorded',
}: {
  label: string;
  value: string | null | undefined;
  mono?: boolean;
  copyable?: boolean;
  emptyLabel?: string;
}) {
  return (
    <div className="alertsEntity">
      <dt>{label}</dt>
      <dd>
        {value ? (
          <>
            <span className={mono ? 'alertsEntityValue alertsMono' : 'alertsEntityValue'} title={value}>{value}</span>
            {copyable ? <CopyValue value={value} label={label} /> : null}
          </>
        ) : (
          <span className="alertsEntityEmpty">{emptyLabel}</span>
        )}
      </dd>
    </div>
  );
}

function CopyValue({ value, label }: { value: string; label: string }) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);
  if (typeof navigator === 'undefined' || !navigator.clipboard) return null;
  return (
    <button
      type="button"
      className="alertsCopy"
      aria-label={copied ? `${label} copied` : `Copy ${label.toLowerCase()}`}
      onClick={() => {
        void navigator.clipboard.writeText(value).then(() => {
          setCopied(true);
          if (timer.current) clearTimeout(timer.current);
          timer.current = setTimeout(() => setCopied(false), 1500);
        }).catch(() => { /* Clipboard refused: the value stays selectable. */ });
      }}
    >
      {copied ? 'Copied' : 'Copy'}
    </button>
  );
}

function truncate(s: string, n: number): string {
  return s.length > n ? `${s.slice(0, n - 1)}…` : s;
}
