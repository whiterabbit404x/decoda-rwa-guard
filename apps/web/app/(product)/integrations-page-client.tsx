'use client';

import Link from 'next/link';
import { useCallback, useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';

import {
  EmptyStateBlocker,
  MetricTile,
  StatusPill,
  TableShell,
  TabStrip,
} from '../components/ui-primitives';
import { usePilotAuth } from '../pilot-auth-context';
import RuntimeSummaryPanel from '../runtime-summary-panel';
import { IntegrationGatewayAgentPanel } from './integration-gateway-agent-panel';
import {
  apiStatusVariant,
  connectionStateVariant,
  fmtExact,
  fmtLatency,
  fmtRelative,
  providerStatusLabel,
  providerStatusVariant,
  webhookStatusVariant,
  type ApiRow,
  type ConnectionRow,
  type ControlPlane,
  type ProviderRow,
  type Recommendation,
  type WebhookRow,
} from './integration-gateway-types';

type TabKey = 'providers' | 'apis' | 'webhooks' | 'connections';

const TABS: Array<{ key: TabKey; label: string }> = [
  { key: 'providers', label: 'Providers' },
  { key: 'apis', label: 'APIs' },
  { key: 'webhooks', label: 'Webhooks' },
  { key: 'connections', label: 'Connections' },
];

const PROVIDER_HEADERS = ['Provider', 'Type', 'Status', 'Role', 'Last Check', 'Latency', 'Targets'];
const API_HEADERS = ['API / Service', 'Authentication', 'Scope', 'Status', 'Credential'];
const WEBHOOK_HEADERS = ['Endpoint', 'Event Types', 'Status', 'Last Delivery', 'Success Rate', 'Failures', 'Retry'];
const CONNECTION_HEADERS = ['Source', 'Via', 'Destination', 'Purpose', 'State', 'Failover', 'Last Verified'];

function DetailRow({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div style={{ display: 'grid', gap: '0.15rem' }}>
      <span className="sectionEyebrow" style={{ fontSize: '0.66rem' }}>{label}</span>
      <span style={{ fontSize: '0.82rem' }}>{value}</span>
    </div>
  );
}

function ProviderDetailDrawer({ provider, onClose }: { provider: ProviderRow; onClose: () => void }) {
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={`${provider.provider} details`}
      style={{ position: 'fixed', inset: 0, zIndex: 60, display: 'flex', justifyContent: 'flex-end' }}
    >
      <button type="button" aria-label="Close" onClick={onClose} style={{ position: 'absolute', inset: 0, background: 'rgba(2,6,23,0.6)', border: 'none' }} />
      <div style={{ position: 'relative', width: 'min(420px, 100%)', height: '100%', overflowY: 'auto', background: 'var(--surface, #0b1220)', padding: '1.25rem', display: 'grid', gap: '0.85rem', alignContent: 'start' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <h3 style={{ margin: 0 }}>{provider.provider}</h3>
          <button type="button" className="btn btn-secondary" style={{ fontSize: '0.75rem' }} onClick={onClose}>Close</button>
        </div>
        <StatusPill label={providerStatusLabel(provider.status)} variant={providerStatusVariant(provider.status)} />
        <DetailRow label="Provider type" value={provider.type} />
        <DetailRow label="Current role" value={provider.role} />
        <DetailRow label="Networks" value={provider.networks.length ? provider.networks.join(', ') : '—'} />
        <DetailRow label="Chains" value={provider.chains.length ? provider.chains.join(', ') : '—'} />
        <DetailRow label="Monitored targets" value={provider.target_count} />
        <DetailRow label="Latency (last success)" value={fmtLatency(provider.latency_ms)} />
        <DetailRow label="Last successful check" value={provider.last_check_at ? fmtExact(provider.last_check_at) : 'No measured observation'} />
        <DetailRow label="Evidence source" value={provider.evidence_source ?? 'none'} />
        {provider.status !== 'healthy' && provider.degraded_reason ? (
          <DetailRow label="Reason" value={provider.degraded_reason.replace(/_/g, ' ')} />
        ) : null}
        <DetailRow
          label="Credential age"
          value={provider.credential_age_days == null ? 'Not available (managed outside Decoda)' : `${provider.credential_age_days} days`}
        />
        <p className="muted" style={{ fontSize: '0.72rem', margin: 0 }}>
          Provider health is read from canonical monitoring evidence — the same source as
          Monitoring Sources and System Health. No secret values are shown.
        </p>
        <Link href="/monitoring-sources" prefetch={false} className="btn btn-secondary" style={{ fontSize: '0.76rem' }}>
          View in Monitoring Sources
        </Link>
      </div>
    </div>
  );
}

export default function IntegrationsPageClient(_props?: { apiUrl?: string }) {
  const { authHeaders, refreshCsrfToken } = usePilotAuth();
  const [data, setData] = useState<ControlPlane | null>(null);
  const [activeTab, setActiveTab] = useState<TabKey>('providers');
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [actionMsg, setActionMsg] = useState('');
  const [actionError, setActionError] = useState('');
  const [healthCheckBusy, setHealthCheckBusy] = useState(false);
  const [recBusyId, setRecBusyId] = useState<string | null>(null);
  const [mobileAgentOpen, setMobileAgentOpen] = useState(false);
  const [selectedProvider, setSelectedProvider] = useState<ProviderRow | null>(null);

  // GET-only load (spec §24): reads canonical state + persisted recommendations. A
  // browser refresh never triggers a scan or mutates the last-scan timestamp.
  const load = useCallback(
    async (opts?: { quiet?: boolean }) => {
      if (!opts?.quiet) setLoading(true);
      try {
        const response = await fetch('/api/integrations/control-plane', { headers: authHeaders(), cache: 'no-store' });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
          const detail = (payload as { detail?: unknown }).detail;
          setLoadError(`Unable to load integrations: ${typeof detail === 'string' ? detail : `HTTP ${response.status}`}`);
          return;
        }
        setData(payload as ControlPlane);
        setLoadError('');
      } catch (error) {
        setLoadError(`Network error loading integrations: ${error instanceof Error ? error.message : 'unknown error'}`);
      } finally {
        if (!opts?.quiet) setLoading(false);
      }
    },
    [authHeaders],
  );

  useEffect(() => {
    void load();
  }, [load]);

  async function mutate(url: string): Promise<Response> {
    await refreshCsrfToken().catch(() => undefined);
    return fetch(url, {
      method: 'POST',
      headers: { ...authHeaders(), 'Content-Type': 'application/json' },
      cache: 'no-store',
      body: '{}',
    });
  }

  async function handleRunHealthCheck() {
    setHealthCheckBusy(true);
    setActionMsg('');
    setActionError('');
    try {
      const response = await mutate('/api/integrations/health-check');
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        const detail = (payload as { detail?: unknown }).detail;
        setActionError(`Health check failed: ${typeof detail === 'string' ? detail : `HTTP ${response.status}`}`);
        return;
      }
      const p = payload as { checked?: number; healthy?: number; degraded?: number; failed?: number; connection_risk?: { level?: string } };
      setActionMsg(
        `Health check completed: ${p.checked ?? 0} provider(s) — ${p.healthy ?? 0} healthy, ${p.degraded ?? 0} degraded, ${p.failed ?? 0} disconnected. Connection risk: ${(p.connection_risk?.level ?? 'unknown').toUpperCase()}.`,
      );
      await load({ quiet: true });
    } catch (error) {
      setActionError(`Network error running health check: ${error instanceof Error ? error.message : 'unknown error'}`);
    } finally {
      setHealthCheckBusy(false);
    }
  }

  async function handleRecAction(rec: Recommendation, action: 'acknowledge' | 'dismiss') {
    setRecBusyId(rec.id);
    setActionMsg('');
    setActionError('');
    try {
      const response = await mutate(`/api/integrations/recommendations/${encodeURIComponent(rec.id)}/${action}`);
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        const detail = (payload as { detail?: unknown }).detail;
        setActionError(`Could not ${action} recommendation: ${typeof detail === 'string' ? detail : `HTTP ${response.status}`}`);
        return;
      }
      setActionMsg(`Recommendation ${action === 'acknowledge' ? 'acknowledged' : 'dismissed'}.`);
      await load({ quiet: true });
    } catch (error) {
      setActionError(`Network error: ${error instanceof Error ? error.message : 'unknown error'}`);
    } finally {
      setRecBusyId(null);
    }
  }

  const summary = data?.summary ?? null;
  const providers = data?.providers ?? [];
  const apis = data?.apis ?? [];
  const webhooks = data?.webhooks ?? [];
  const connections = data?.connections ?? [];
  const canManage = Boolean(data?.permissions.can_manage);

  const hasAnyIntegration = useMemo(
    () => providers.length > 0 || webhooks.length > 0 || apis.some((a) => a.credential_configured),
    [providers, webhooks, apis],
  );

  const agentPanel = (
    <IntegrationGatewayAgentPanel
      data={data}
      loading={loading}
      healthCheckBusy={healthCheckBusy}
      recBusyId={recBusyId}
      onRunHealthCheck={() => void handleRunHealthCheck()}
      onAcknowledge={(rec) => void handleRecAction(rec, 'acknowledge')}
      onDismiss={(rec) => void handleRecAction(rec, 'dismiss')}
    />
  );

  return (
    <main className="productPage">
      <RuntimeSummaryPanel />

      {/* ── Header ─────────────────────────────────────────── */}
      <div className="listHeader" style={{ marginBottom: '1rem', alignItems: 'flex-start', flexWrap: 'wrap', gap: '0.75rem' }}>
        <div>
          <h1 style={{ margin: 0, fontSize: '1.45rem', fontWeight: 700 }}>Integrations</h1>
          <p className="muted" style={{ margin: '0.35rem 0 0', fontSize: '0.9rem', maxWidth: 640 }}>
            Operational control plane for every external dependency Decoda relies on — provider
            health, API credentials, outbound webhooks, and connection risk.
          </p>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}>
          {data?.last_scan?.completed_at ? (
            <span title={fmtExact(data.last_scan.completed_at)} style={{ fontSize: '0.74rem', color: 'var(--text-muted)' }}>
              Last scan {fmtRelative(data.last_scan.completed_at)}
            </span>
          ) : null}
          <button
            type="button"
            className="btn btn-secondary"
            style={{ fontSize: '0.8rem' }}
            disabled={healthCheckBusy || !canManage}
            title={canManage ? undefined : 'Requires the Manage Integrations permission'}
            onClick={() => void handleRunHealthCheck()}
          >
            {healthCheckBusy ? 'Running…' : 'Run Health Check'}
          </button>
          <Link href="/monitoring-sources/targets" prefetch={false} className="btn btn-primary" style={{ fontSize: '0.8rem' }}>
            Add Provider
          </Link>
        </div>
      </div>

      {loadError ? <p className="statusLine" style={{ color: 'var(--danger-fg)' }}>{loadError}</p> : null}
      {actionError ? <p className="statusLine" style={{ color: 'var(--danger-fg)', fontSize: '0.85rem' }}>{actionError}</p> : null}
      {actionMsg ? <p className="statusLine" style={{ color: 'var(--success-fg)', fontSize: '0.85rem' }}>{actionMsg}</p> : null}

      {/* ── Summary metrics (derived from canonical data §4) ── */}
      <div className="metricRow" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: '0.75rem', marginBottom: '1rem' }}>
        <MetricTile label="Total Integrations" value={loading && !summary ? '—' : summary?.total_integrations ?? 0} meta={`${providers.length} provider(s)`} />
        <MetricTile label="Healthy" value={loading && !summary ? '—' : summary?.healthy ?? 0} meta="Providers" />
        <MetricTile label="Degraded" value={loading && !summary ? '—' : summary?.degraded ?? 0} meta={summary && summary.degraded > 0 ? 'Needs attention' : 'Providers'} />
        <MetricTile label="Disconnected" value={loading && !summary ? '—' : summary?.disconnected ?? 0} meta={summary && summary.disconnected > 0 ? 'Failover required' : 'Providers'} />
        <MetricTile label="Recommendations" value={loading && !summary ? '—' : summary?.recommendations ?? 0} meta="Open" />
      </div>

      <div style={{ display: 'flex', gap: '1.25rem', alignItems: 'flex-start', flexWrap: 'wrap' }}>
        {/* ── Main content ─────────────────────────────────── */}
        <div style={{ flex: '1 1 620px', minWidth: 0 }}>
          <TabStrip tabs={TABS} active={activeTab} onChange={(key) => setActiveTab(key as TabKey)} />

          {activeTab === 'providers' ? (
            <div role="tabpanel" aria-label="Providers">
              {!loading && providers.length === 0 ? (
                <EmptyStateBlocker
                  title="No providers are configured"
                  body="Connect an RPC, oracle, custody platform, or external API to begin monitoring integration health."
                  ctaHref="/monitoring-sources/targets"
                  ctaLabel="Add Provider"
                />
              ) : (
                <TableShell headers={PROVIDER_HEADERS} compact>
                  {loading && providers.length === 0 ? (
                    <tr><td colSpan={PROVIDER_HEADERS.length} style={{ textAlign: 'center', color: 'var(--text-muted)', padding: '2rem' }}>Loading integrations…</td></tr>
                  ) : (
                    providers.map((provider) => (
                      <tr key={provider.integration_key} style={{ cursor: 'pointer' }} onClick={() => setSelectedProvider(provider)}>
                        <td style={{ fontWeight: 600 }}>{provider.provider}</td>
                        <td>{provider.type}</td>
                        <td>
                          <StatusPill label={providerStatusLabel(provider.status)} variant={providerStatusVariant(provider.status)} />
                          {provider.status !== 'healthy' && provider.degraded_reason ? (
                            <div className="muted" style={{ fontSize: '0.66rem', marginTop: '0.15rem' }}>{provider.degraded_reason.replace(/_/g, ' ')}</div>
                          ) : null}
                        </td>
                        <td style={{ textTransform: 'capitalize' }}>{provider.role}</td>
                        <td style={{ whiteSpace: 'nowrap' }} title={fmtExact(provider.last_check_at)}>{fmtRelative(provider.last_check_at)}</td>
                        <td style={{ whiteSpace: 'nowrap' }}>{fmtLatency(provider.latency_ms)}</td>
                        <td>{provider.target_count}</td>
                      </tr>
                    ))
                  )}
                </TableShell>
              )}
            </div>
          ) : null}

          {activeTab === 'apis' ? (
            <div role="tabpanel" aria-label="APIs">
              {!loading && apis.length === 0 ? (
                <EmptyStateBlocker title="No API integrations" body="API-based service integrations appear here once configured." />
              ) : (
                <TableShell headers={API_HEADERS} compact>
                  {apis.map((api: ApiRow) => (
                    <tr key={api.integration_key}>
                      <td style={{ fontWeight: 600 }}>{api.name}</td>
                      <td>{api.authentication}</td>
                      <td style={{ textTransform: 'capitalize' }}>{api.scope}</td>
                      <td><StatusPill label={api.status} variant={apiStatusVariant(api.status)} /></td>
                      <td>{api.credential_configured ? 'Configured' : 'Missing'}</td>
                    </tr>
                  ))}
                </TableShell>
              )}
              <p className="muted" style={{ fontSize: '0.72rem', marginTop: '0.6rem' }}>
                Credentials are never returned to the browser — only a configured / missing flag is shown.
              </p>
            </div>
          ) : null}

          {activeTab === 'webhooks' ? (
            <div role="tabpanel" aria-label="Webhooks">
              {!loading && webhooks.length === 0 ? (
                <EmptyStateBlocker
                  title="No webhooks configured"
                  body="Configure an outbound webhook to deliver alert and incident events to an external system (e.g. a SIEM)."
                  ctaHref="/settings/notifications"
                  ctaLabel="Configure notifications"
                />
              ) : (
                <TableShell headers={WEBHOOK_HEADERS} compact>
                  {webhooks.map((webhook: WebhookRow) => (
                    <tr key={webhook.integration_key}>
                      <td style={{ fontWeight: 600 }} title={webhook.description ?? undefined}>{webhook.endpoint}</td>
                      <td>{webhook.event_types.length ? webhook.event_types.join(', ') : '—'}</td>
                      <td><StatusPill label={webhook.status} variant={webhookStatusVariant(webhook.status)} /></td>
                      <td style={{ whiteSpace: 'nowrap' }} title={fmtExact(webhook.last_delivery_at)}>{fmtRelative(webhook.last_delivery_at)}</td>
                      <td>{webhook.success_rate == null ? '—' : `${webhook.success_rate}%`}</td>
                      <td>{webhook.failures}</td>
                      <td style={{ textTransform: 'capitalize' }}>{webhook.retry_state}</td>
                    </tr>
                  ))}
                </TableShell>
              )}
            </div>
          ) : null}

          {activeTab === 'connections' ? (
            <div role="tabpanel" aria-label="Connections">
              {!loading && connections.length === 0 ? (
                <EmptyStateBlocker title="No connections" body="Connection dependencies appear once providers or webhooks are configured." />
              ) : (
                <TableShell headers={CONNECTION_HEADERS} compact>
                  {connections.map((connection: ConnectionRow) => (
                    <tr key={connection.integration_key}>
                      <td>{connection.source}</td>
                      <td style={{ fontWeight: 600 }}>{connection.via}</td>
                      <td>{connection.destination}</td>
                      <td>{connection.purpose}</td>
                      <td><StatusPill label={connection.state} variant={connectionStateVariant(connection.state)} /></td>
                      <td style={{ textTransform: 'capitalize' }}>{connection.failover}</td>
                      <td style={{ whiteSpace: 'nowrap' }} title={fmtExact(connection.last_verified_at)}>{fmtRelative(connection.last_verified_at)}</td>
                    </tr>
                  ))}
                </TableShell>
              )}
            </div>
          ) : null}

          {!hasAnyIntegration && !loading ? (
            <p className="muted" style={{ fontSize: '0.75rem', marginTop: '0.75rem' }}>
              No external integrations are configured for this workspace yet.
            </p>
          ) : null}
        </div>

        {/* ── Right rail (desktop) ──────────────────────────── */}
        <aside style={{ flex: '0 1 320px', minWidth: '280px' }} className="integrationAgentRail">
          {agentPanel}
        </aside>
      </div>

      {/* ── Mobile agent drawer toggle ────────────────────── */}
      <button
        type="button"
        className="btn btn-primary integrationAgentMobileToggle"
        onClick={() => setMobileAgentOpen(true)}
        style={{ position: 'fixed', bottom: 16, right: 16, zIndex: 40, display: 'none' }}
      >
        Agent
      </button>
      {mobileAgentOpen ? (
        <div role="dialog" aria-modal="true" aria-label="Integration Gateway Agent" style={{ position: 'fixed', inset: 0, zIndex: 60, display: 'flex', justifyContent: 'flex-end' }}>
          <button type="button" aria-label="Close" onClick={() => setMobileAgentOpen(false)} style={{ position: 'absolute', inset: 0, background: 'rgba(2,6,23,0.6)', border: 'none' }} />
          <div style={{ position: 'relative', width: 'min(360px, 100%)', height: '100%', overflowY: 'auto', background: 'var(--surface, #0b1220)', padding: '1rem' }}>
            <button type="button" className="btn btn-secondary" style={{ marginBottom: '0.75rem', fontSize: '0.75rem' }} onClick={() => setMobileAgentOpen(false)}>Close</button>
            {agentPanel}
          </div>
        </div>
      ) : null}

      {selectedProvider ? (
        <ProviderDetailDrawer provider={selectedProvider} onClose={() => setSelectedProvider(null)} />
      ) : null}

      <style>{`
        @media (max-width: 900px) {
          .integrationAgentRail { display: none; }
          .integrationAgentMobileToggle { display: inline-flex !important; }
        }
      `}</style>
    </main>
  );
}
