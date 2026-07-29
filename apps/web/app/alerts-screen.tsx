'use client';

import Link from 'next/link';
import { Suspense, useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';

import { Select, StatusPill, TableShell } from './components/ui-primitives';
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

export default function AlertsScreen() {
  // useSearchParams requires a Suspense boundary; the page owns the <h1>.
  return (
    <Suspense fallback={<div className="muted" style={{ padding: '1.5rem 0' }}>Loading alerts…</div>}>
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
    const headers = authHeaders();
    if (!headers.Authorization) {
      setError('Your session is missing or expired. Please sign in again.');
      setLoading(false);
      return;
    }
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
  const alerts = summary?.alerts ?? [];
  const pagination = summary?.pagination;
  const triage = summary?.triage ?? null;

  const selectedAlert = useMemo(
    () => alerts.find((a) => a.id === selectedAlertId) ?? null,
    [alerts, selectedAlertId],
  );

  const clearFilters = useCallback(() => {
    router.replace('/alerts', { scroll: false });
    setSearchInput('');
  }, [router]);

  const assetOptions = useMemo(() => ([
    { value: '', label: 'All assets' },
    ...(summary?.filters.assets ?? []).map((a) => ({ value: a.value, label: a.label })),
  ]), [summary]);

  const statusOptions = useMemo(() => ([
    { value: '', label: 'All statuses' },
    ...(summary?.filters.statuses ?? []).map((s) => ({ value: s, label: statusLabel(s) })),
  ]), [summary]);

  return (
    <div style={{ maxWidth: '1680px', width: '100%' }}>
      {/* Two-column split: ~75% alerts, ~25% triage agent. */}
      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 3fr) minmax(300px, 1fr)', gap: '1.25rem', alignItems: 'start' }}>
        {/* ── Main panel ─────────────────────────────────────────────── */}
        <main style={{ minWidth: 0 }}>
          {/* Severity tabs with real counts (from the same read model as the rows). */}
          <div className="buttonRow sharedTabStrip" role="tablist" aria-label="Severity" style={{ marginBottom: '1rem' }}>
            {SEVERITY_TABS.map((tab) => (
              <button
                key={tab.key || 'all'}
                type="button"
                role="tab"
                aria-selected={severity === tab.key}
                className={severity === tab.key ? 'activeTab' : ''}
                onClick={() => setUrl({ severity: tab.key || null }, { resetPage: true })}
                data-testid={`severity-tab-${tab.key || 'all'}`}
              >
                {tab.label}
                <span
                  aria-label={`${totals[tab.countKey]} alerts`}
                  style={{
                    marginLeft: '0.4rem', fontSize: '0.72rem', padding: '0.05rem 0.4rem',
                    borderRadius: '999px', background: 'rgba(148,163,184,0.18)',
                  }}
                >
                  {totals[tab.countKey]}
                </span>
              </button>
            ))}
          </div>

          {/* Filter toolbar. */}
          <div className="buttonRow" style={{ marginBottom: '1rem', alignItems: 'center', flexWrap: 'wrap', gap: '0.5rem' }}>
            <input
              placeholder="Search title, asset, tx, contract, actor, cluster…"
              value={searchInput}
              onChange={(e) => onSearchChange(e.target.value)}
              style={{ flex: '1 1 240px', minWidth: '200px' }}
              aria-label="Search alerts"
              data-testid="alerts-search"
            />
            <div style={{ minWidth: '180px' }}>
              <Select
                value={assetId}
                onValueChange={(v) => setUrl({ asset: v || null }, { resetPage: true })}
                options={assetOptions}
                ariaLabel="Filter by asset"
                testId="asset-filter"
              />
            </div>
            <div style={{ minWidth: '170px' }}>
              <Select
                value={statusValue}
                onValueChange={(v) => setUrl({ status: v || null }, { resetPage: true })}
                options={statusOptions}
                ariaLabel="Filter by status"
                testId="status-filter"
              />
            </div>
            {hasFilters ? (
              <button type="button" className="btn btn-ghost" onClick={clearFilters} data-testid="clear-filters">
                Clear filters
              </button>
            ) : null}
          </div>

          {clusterId ? (
            <div className="statusLine" role="status" style={{ marginBottom: '0.75rem', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
              <span>Filtered to a single cluster.</span>
              <button type="button" className="btn btn-ghost" style={{ padding: '0.1rem 0.5rem' }} onClick={() => setUrl({ cluster: null }, { resetPage: true })}>
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
          />

          {/* Pagination — accurate range + total, backend-paginated. */}
          {pagination && pagination.total > 0 ? (
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginTop: '1rem', gap: '1rem', flexWrap: 'wrap' }}>
              <span className="muted" style={{ fontSize: '0.8rem' }} data-testid="pagination-range">
                Showing {pagination.showing_from}–{pagination.showing_to} of {pagination.total}
              </span>
              <div className="buttonRow" style={{ gap: '0.5rem' }}>
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
        />
      ) : null}
    </div>
  );
}

/* ── Table region with all required states ──────────────────────────────── */
function AlertsTableRegion({
  loading, error, summary, alerts, hasFilters, selectedAlertId, onSelect, onRetry,
}: {
  loading: boolean;
  error: string;
  summary: Screen6Summary | null;
  alerts: Screen6Alert[];
  hasFilters: boolean;
  selectedAlertId: string | null;
  onSelect: (id: string) => void;
  onRetry: () => void;
}) {
  if (loading && !summary) {
    return <div className="emptyStatePanel sharedEmptyStateBlocker" data-testid="alerts-loading"><h4>Loading active alerts…</h4></div>;
  }
  if (error) {
    return (
      <div className="emptyStatePanel sharedEmptyStateBlocker" data-testid="alerts-error">
        <h4>Couldn’t load alerts</h4>
        <p className="muted">{error}</p>
        <button type="button" className="btn btn-secondary" onClick={onRetry}>Retry</button>
      </div>
    );
  }
  if (alerts.length === 0) {
    // Distinguish: empty workspace vs no active alerts vs no results for filters.
    if (hasFilters) {
      return (
        <div className="emptyStatePanel sharedEmptyStateBlocker" data-testid="alerts-empty-filters">
          <h4>No alerts match the current filters</h4>
          <p className="muted">Adjust or clear the filters above to see more results.</p>
        </div>
      );
    }
    if ((summary?.active_alert_count ?? 0) === 0) {
      return (
        <div className="emptyStatePanel sharedEmptyStateBlocker" data-testid="alerts-empty">
          <h4>No active alerts</h4>
          <p className="muted">There are no active alerts in this workspace. New alerts appear here as telemetry and detections arrive.</p>
        </div>
      );
    }
  }
  return (
    <TableShell headers={TABLE_HEADERS} compact>
      {alerts.map((alert) => {
        const isSelected = alert.id === selectedAlertId;
        return (
          <tr
            key={alert.id}
            onClick={() => onSelect(alert.id)}
            style={{ cursor: 'pointer', background: isSelected ? 'rgba(59,130,246,0.08)' : undefined }}
            data-testid="alert-row"
          >
            <td><StatusPill label={severityLabel(alert.severity)} variant={severityVariant(alert.severity)} /></td>
            <td style={{ maxWidth: '320px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {alert.title}
              {alert.detection_family_label ? (
                <div className="muted" style={{ fontSize: '0.7rem' }}>{alert.detection_family_label}</div>
              ) : null}
            </td>
            <td style={{ fontSize: '0.82rem' }}>
              <span style={{ color: alert.asset_known ? undefined : 'var(--text-secondary, #94a3b8)' }}>{alert.asset_name}</span>
              {alert.tx_hash ? (
                <div style={{ fontFamily: 'monospace', fontSize: '0.7rem', color: '#94a3b8' }} title={alert.tx_hash}>
                  {shortHash(alert.tx_hash)}
                </div>
              ) : null}
            </td>
            <td><StatusPill label={statusLabel(alert.status)} variant={statusVariant(alert.status)} /></td>
            <td style={{ fontSize: '0.78rem', whiteSpace: 'nowrap' }} title={exactTime(alert.occurred_at)}>
              {relativeTime(alert.occurred_at)}
            </td>
            <td style={{ fontSize: '0.78rem' }}>
              {alert.cluster ? (
                <button
                  type="button"
                  className="btn btn-ghost"
                  style={{ padding: '0.1rem 0.4rem', fontSize: '0.72rem' }}
                  title={alert.cluster.title ?? undefined}
                  onClick={(e) => { e.stopPropagation(); onSelect(alert.id); }}
                >
                  {truncate(alert.cluster.title ?? 'Cluster', 22)}
                </button>
              ) : (
                <span className="muted" data-testid="unclustered">Unclustered</span>
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
    <aside className="dataCard sharedSurfaceCard" style={{ padding: '1.15rem', position: 'sticky', top: '1rem' }} aria-label="AI Alert Triage Agent" data-testid="triage-panel">
      <p className="eyebrow" style={{ marginBottom: '0.15rem', fontSize: '0.7rem' }}>Autonomous Agent</p>
      <h3 style={{ margin: '0 0 0.35rem', fontSize: '1rem' }}>AI Alert Triage Agent</h3>

      {loading ? (
        <p className="muted" style={{ fontSize: '0.82rem' }}>Loading triage state…</p>
      ) : (
        <>
          {/* Summary line + operational facts. */}
          <p style={{ fontSize: '0.85rem', margin: '0 0 0.6rem', lineHeight: 1.4 }} data-testid="triage-summary">
            {triageSummaryText(triage)}
          </p>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.4rem', marginBottom: '0.6rem' }}>
            <StatusPill label={triageModeLabel(triage?.mode)} variant={triageModeVariant(triage?.mode)} />
            <StatusPill label={display.statusLabel} variant={display.statusVariant} />
          </div>
          <div style={{ fontSize: '0.76rem', color: 'var(--text-secondary, #94a3b8)', marginBottom: '0.75rem', display: 'grid', gap: '0.2rem' }}>
            <div>
              Last successful triage:{' '}
              <span title={exactTime(triage?.last_success_at)}>
                {triage?.last_success_at ? relativeTime(triage.last_success_at) : 'never'}
              </span>
            </div>
            <div data-testid="unclustered-count">
              Unclustered active alerts:{' '}
              {triage?.unclustered_count === null || triage?.unclustered_count === undefined ? 'Not calculated' : triage.unclustered_count}
            </div>
            {triage && !triage.ai_available ? (
              <div title="No live AI provider is configured; clustering and recommendations remain deterministic.">
                AI provider: unavailable — deterministic rules only
              </div>
            ) : null}
            {triage?.state === 'degraded' && triage.last_run?.error_code ? (
              <div style={{ color: '#f59e0b' }} data-testid="triage-degraded">
                Last run failed ({triage.last_run.error_code}); showing the previous successful result.
              </div>
            ) : null}
          </div>

          {/* Top recommendation. */}
          {top ? (
            <div style={{ borderTop: '1px solid rgba(148,163,184,0.15)', paddingTop: '0.7rem', marginBottom: '0.6rem' }} data-testid="top-recommendation">
              <p className="sectionEyebrow" style={{ marginBottom: '0.3rem' }}>Top recommendation</p>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', flexWrap: 'wrap', marginBottom: '0.3rem' }}>
                <StatusPill label={severityLabel(top.derived_severity)} variant={severityVariant(top.derived_severity)} />
                <StatusPill label={recommendationLabel(top.recommendation_category)} variant="info" />
              </div>
              <p style={{ fontSize: '0.85rem', margin: '0 0 0.25rem', fontWeight: 600 }}>{top.title}</p>
              <div style={{ fontSize: '0.76rem', color: 'var(--text-secondary, #94a3b8)', marginBottom: '0.35rem' }}>
                <div>Affected asset: {top.primary_asset_name || 'Unassigned asset'}</div>
                <div>{top.member_count} alert{top.member_count === 1 ? '' : 's'} in this cluster</div>
                <div>
                  Confidence:{' '}
                  <span data-testid="top-confidence">{confidenceText(top.confidence, top.confidence_band)}</span>
                </div>
              </div>
              {/* AI summary is rendered as plain text via JSX interpolation — never as raw HTML. */}
              {top.recommendation_summary ? (
                <p style={{ fontSize: '0.8rem', margin: '0 0 0.35rem', lineHeight: 1.4 }}>{top.recommendation_summary}</p>
              ) : null}
              {top.recommendation_source === 'ai_assisted' ? (
                <p className="muted" style={{ fontSize: '0.7rem', margin: 0 }}>AI-generated from stored cluster evidence.</p>
              ) : (
                <p className="muted" style={{ fontSize: '0.7rem', margin: 0 }}>Generated from stored cluster evidence.</p>
              )}
              {/* Reasoning chips. */}
              {top.reason_labels && top.reason_labels.length > 0 ? (
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.3rem', marginTop: '0.5rem' }} data-testid="triage-reasons">
                  {top.reason_labels.slice(0, 6).map((r) => (
                    <span key={r} style={{ fontSize: '0.68rem', padding: '0.1rem 0.45rem', borderRadius: '999px', background: 'rgba(148,163,184,0.14)' }}>{r}</span>
                  ))}
                </div>
              ) : null}
            </div>
          ) : triage?.schema_ready ? (
            <p className="muted" style={{ fontSize: '0.82rem', borderTop: '1px solid rgba(148,163,184,0.15)', paddingTop: '0.7rem' }} data-testid="no-clusters">
              {triage.state === 'not_run'
                ? 'No triage has run yet. Run triage to group related alerts into clusters.'
                : 'No related-alert clusters were found in the current active set.'}
            </p>
          ) : (
            <p className="muted" style={{ fontSize: '0.82rem' }} data-testid="triage-provisioning">
              Alert triage storage is provisioning.
            </p>
          )}

          {/* Actions. */}
          <div className="buttonRow" style={{ gap: '0.5rem', marginTop: '0.6rem' }}>
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
            <p className="muted" style={{ fontSize: '0.7rem', marginTop: '0.4rem' }}>{display.hint}</p>
          ) : null}
          {triageMessage ? (
            <p className="statusLine" style={{ fontSize: '0.76rem', marginTop: '0.5rem' }} data-testid="triage-message">{triageMessage}</p>
          ) : null}
        </>
      )}
    </aside>
  );
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
  return (
    <div style={overlayStyle} onClick={onClose} role="dialog" aria-modal="true" aria-label="Cluster review" data-testid="cluster-drawer">
      <aside style={drawerStyle} onClick={(e) => e.stopPropagation()}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.75rem' }}>
          <h3 style={{ margin: 0, fontSize: '1rem' }}>Active clusters ({clusters.length})</h3>
          <button type="button" className="btn btn-ghost" onClick={onClose} aria-label="Close">✕</button>
        </div>
        <p className="muted" style={{ fontSize: '0.78rem', marginTop: 0 }}>
          Ordered by triage priority. Select a cluster to filter its member alerts. Response actions are not run from this screen.
        </p>
        <div style={{ display: 'grid', gap: '0.6rem', marginTop: '0.75rem' }}>
          {clusters.map((c) => (
            <button
              key={c.id}
              type="button"
              onClick={() => onSelect(c.id)}
              data-testid="cluster-item"
              style={{
                textAlign: 'left', padding: '0.7rem', borderRadius: '10px', cursor: 'pointer',
                border: `1px solid ${activeClusterId === c.id ? 'rgba(59,130,246,0.6)' : 'rgba(148,163,184,0.2)'}`,
                background: activeClusterId === c.id ? 'rgba(59,130,246,0.08)' : 'transparent',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', flexWrap: 'wrap', marginBottom: '0.3rem' }}>
                <StatusPill label={severityLabel(c.derived_severity)} variant={severityVariant(c.derived_severity)} />
                <StatusPill label={recommendationLabel(c.recommendation_category)} variant="info" />
                <StatusPill label={confidenceText(c.confidence, c.confidence_band)} variant={confidenceVariant(c.confidence_band)} />
              </div>
              <div style={{ fontSize: '0.86rem', fontWeight: 600 }}>{c.title}</div>
              <div className="muted" style={{ fontSize: '0.75rem' }}>
                {c.primary_asset_name || 'Unassigned asset'} · {c.member_count} alert{c.member_count === 1 ? '' : 's'}
              </div>
              {c.recommendation_summary ? (
                <div style={{ fontSize: '0.76rem', marginTop: '0.3rem', lineHeight: 1.4 }}>{c.recommendation_summary}</div>
              ) : null}
            </button>
          ))}
        </div>
      </aside>
    </div>
  );
}

/* ── Alert detail drawer (reuses the canonical escalate endpoint) ───────── */
function AlertDetailDrawer({
  alert, apiUrl, authHeaders, onClose, onViewCluster,
}: {
  alert: Screen6Alert;
  apiUrl: string;
  authHeaders: () => Record<string, string>;
  onClose: () => void;
  onViewCluster: (id: string) => void;
}) {
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);

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

  return (
    <div style={overlayStyle} onClick={onClose} role="dialog" aria-modal="true" aria-label="Alert detail" data-testid="alert-detail">
      <aside style={drawerStyle} onClick={(e) => e.stopPropagation()}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.5rem' }}>
          <p className="eyebrow" style={{ margin: 0, fontSize: '0.7rem' }}>Alert Detail</p>
          <button type="button" className="btn btn-ghost" onClick={onClose} aria-label="Close">✕</button>
        </div>
        <h3 style={{ margin: '0 0 0.75rem', fontSize: '1rem', lineHeight: 1.35 }}>{alert.title}</h3>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.6rem 1rem', marginBottom: '0.75rem' }}>
          <Field label="Severity"><StatusPill label={severityLabel(alert.severity)} variant={severityVariant(alert.severity)} /></Field>
          <Field label="Status"><StatusPill label={statusLabel(alert.status)} variant={statusVariant(alert.status)} /></Field>
          <Field label="Asset"><span>{alert.asset_name}</span></Field>
          <Field label="Detection"><span>{alert.detection_family_label || '—'}</span></Field>
          <Field label="Occurred"><span title={exactTime(alert.occurred_at)}>{relativeTime(alert.occurred_at)}</span></Field>
          <Field label="Cluster">
            {alert.cluster ? (
              <button type="button" className="btn btn-ghost" style={{ padding: '0.1rem 0.4rem', fontSize: '0.72rem' }} onClick={() => onViewCluster(alert.cluster!.id)}>
                {truncate(alert.cluster.title ?? 'Cluster', 24)}
              </button>
            ) : <span className="muted">Unclustered</span>}
          </Field>
        </div>
        {alert.tx_hash ? (
          <div style={{ marginBottom: '0.75rem' }}>
            <p className="tableMeta" style={{ marginBottom: '0.1rem' }}>Transaction</p>
            <p style={{ fontFamily: 'monospace', fontSize: '0.72rem', margin: 0, wordBreak: 'break-all' }}>{alert.tx_hash}</p>
          </div>
        ) : null}
        <div className="buttonRow" style={{ gap: '0.5rem', marginTop: '0.5rem' }}>
          {alert.incident_id ? (
            <Link href={`/incidents/${alert.incident_id}`} prefetch={false} className="btn btn-secondary">View Incident</Link>
          ) : (
            <button type="button" className="btn btn-primary" onClick={() => void escalate()} disabled={busy} data-testid="escalate-alert">
              {busy ? 'Opening…' : 'Escalate to Incident'}
            </button>
          )}
        </div>
        {message ? <p className="statusLine" style={{ fontSize: '0.76rem', marginTop: '0.5rem' }}>{message}</p> : null}
      </aside>
    </div>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <p className="tableMeta" style={{ marginBottom: '0.15rem' }}>{label}</p>
      <div style={{ fontSize: '0.82rem' }}>{children}</div>
    </div>
  );
}

function truncate(s: string, n: number): string {
  return s.length > n ? `${s.slice(0, n - 1)}…` : s;
}

const overlayStyle: CSSProperties = {
  position: 'fixed', inset: 0, background: 'rgba(2,6,23,0.55)', zIndex: 50,
  display: 'flex', justifyContent: 'flex-end',
};
const drawerStyle: CSSProperties = {
  width: 'min(440px, 100%)', height: '100%', overflowY: 'auto', padding: '1.25rem',
  background: 'var(--surface, #0f172a)', borderLeft: '1px solid rgba(148,163,184,0.2)',
};
