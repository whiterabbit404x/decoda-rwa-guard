'use client';

import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';

import { usePilotAuth } from '../pilot-auth-context';
import RuntimeSummaryPanel from '../runtime-summary-panel';

type TabKey = 'providers' | 'api-keys' | 'webhooks' | 'connections';

const TABS: Array<{ key: TabKey; label: string }> = [
  { key: 'providers', label: 'Providers' },
  { key: 'api-keys', label: 'API Keys' },
  { key: 'webhooks', label: 'Webhooks' },
  { key: 'connections', label: 'Connections' },
];

const PROVIDER_HEADERS = ['Provider', 'Type', 'Status', 'Last Sync', 'Last Error', 'Actions'] as const;
const API_KEY_HEADERS = ['Key Name', 'Scope', 'Status', 'Created', 'Last Used', 'Actions'] as const;
const WEBHOOK_HEADERS = ['Webhook', 'Event Types', 'Status', 'Last Delivery', 'Failure Rate', 'Actions'] as const;
const CONNECTION_HEADERS = ['Connection', 'Source', 'Destination', 'Status', 'Latency', 'Last Check', 'Actions'] as const;

type HealthRecord = {
  status?: string | null;
  message?: string | null;
  checked_at?: string | null;
  last_check_at?: string | null;
  last_sync_at?: string | null;
  latency_ms?: number | null;
};

type IntegrationHealth = Record<string, HealthRecord | undefined>;

type ProviderRow = {
  id: string;
  provider: string;
  type: string;
  status: 'Connected' | 'Degraded' | 'Disconnected' | 'Not Configured' | 'Disabled' | 'Error' | 'Unknown';
  lastSync: string | null;
  lastError: string | null;
  linkedTargets: number;
  linkedSystems: number;
  evidenceSourceCapability: string;
  authenticationStatus: string;
  nextAction: string;
};

type ApiKeyRow = {
  id: string;
  keyName: string;
  keyPrefix: string | null;
  scope: string;
  status: 'Active' | 'Expiring Soon' | 'Revoked' | 'Disabled' | 'Never Used' | 'Unknown';
  created: string | null;
  lastUsed: string | null;
  rotationStatus: string;
  owner: string;
  linkedIntegration: string | null;
};

type WebhookRow = {
  id: string;
  webhook: string;
  eventTypes: string[];
  status: 'Active' | 'Failing' | 'Disabled' | 'Pending Verification' | 'Unknown';
  lastDelivery: string | null;
  lastError: string | null;
  failureRate: string;
  signingSecretStatus: string;
  retryPolicy: string;
};

type ConnectionRow = {
  id: string;
  connection: string;
  source: string;
  destination: string;
  status: 'Healthy' | 'Degraded' | 'Offline' | 'Not Configured' | 'Unknown';
  latency: string;
  lastCheck: string | null;
  lastError: string | null;
  healthReason: string;
  linkedProvider: string;
  linkedTargetSystem: string;
};

function normaliseStatus(value?: string | null): string {
  return String(value ?? '').trim().toLowerCase();
}

function providerStatusFromBackend(record?: HealthRecord): ProviderRow['status'] {
  const status = normaliseStatus(record?.status);

  if (!record || !status) return 'Unknown';
  if (['ok', 'healthy', 'connected'].includes(status)) return 'Connected';
  if (['degraded', 'warning', 'limited'].includes(status)) return 'Degraded';
  if (['disabled'].includes(status)) return 'Disabled';
  if (['not_configured', 'not configured', 'missing'].includes(status)) return 'Not Configured';
  if (['disconnected', 'offline'].includes(status)) return 'Disconnected';
  if (['error', 'failed', 'failure'].includes(status)) return 'Error';

  return 'Unknown';
}

function connectionStatusFromBackend(record?: HealthRecord, lastCheck?: string | null): ConnectionRow['status'] {
  const status = normaliseStatus(record?.status);

  if (!record || !status || !lastCheck) return 'Unknown';
  if (['ok', 'healthy', 'connected'].includes(status)) return 'Healthy';
  if (['degraded', 'warning', 'limited'].includes(status)) return 'Degraded';
  if (['offline', 'disconnected', 'error', 'failed', 'failure'].includes(status)) return 'Offline';
  if (['not_configured', 'not configured', 'missing'].includes(status)) return 'Not Configured';

  return 'Unknown';
}

function apiKeyStatusFromBackend(key: Record<string, unknown>): ApiKeyRow['status'] {
  const status = normaliseStatus(String(key.status ?? ''));

  if (key.revoked_at || status === 'revoked') return 'Revoked';
  if (status === 'disabled') return 'Disabled';
  if (['expiring_soon', 'expiring soon'].includes(status)) return 'Expiring Soon';
  if (!key.last_used_at) return 'Never Used';
  if (status === 'active') return 'Active';

  return status ? 'Unknown' : 'Unknown';
}

function webhookStatusFromBackend(webhook: Record<string, unknown>): WebhookRow['status'] {
  const status = normaliseStatus(String(webhook.status ?? ''));

  if (webhook.enabled === false || status === 'disabled') return 'Disabled';
  if (['failing', 'failed', 'error'].includes(status) || Number(webhook.failure_count ?? 0) > 0) return 'Failing';
  if (['pending_verification', 'pending verification'].includes(status)) return 'Pending Verification';
  if (status === 'active' || webhook.enabled === true) return 'Active';

  return 'Unknown';
}

function providerTypeFor(key: string): string {
  const normalised = key.toLowerCase();

  if (normalised.includes('rpc')) return 'Blockchain RPC';
  if (normalised.includes('index')) return 'Indexer';
  if (normalised.includes('oracle')) return 'Oracle';
  if (normalised.includes('compliance')) return 'Compliance Source';
  if (normalised.includes('custody')) return 'Custody Provider';
  if (normalised.includes('stable')) return 'Stablecoin Provider';
  if (normalised.includes('webhook')) return 'Webhook';
  if (normalised.includes('stream')) return 'Internal Stream';

  return 'Other';
}

function titleCase(value: string): string {
  return value
    .replace(/[-_]+/g, ' ')
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function formatDate(value?: string | null): string {
  if (!value) return '-';

  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return '-';

  return parsed.toLocaleString();
}

function formatRelative(value?: string | null): string {
  if (!value) return '-';

  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return '-';

  const seconds = Math.max(0, Math.floor((Date.now() - parsed.getTime()) / 1000));
  if (seconds < 60) return String(seconds) + 's ago';

  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return String(minutes) + 'm ago';

  const hours = Math.floor(minutes / 60);
  if (hours < 24) return String(hours) + 'h ago';

  return String(Math.floor(hours / 24)) + 'd ago';
}

function maskUrl(value?: string | null): string {
  if (!value) return '-';

  try {
    const url = new URL(value);
    return url.protocol + '//' + url.hostname + '/***';
  } catch {
    return value.length > 32 ? value.slice(0, 32) + '...' : value;
  }
}

function statusClass(status: string): string {
  const normalized = status.toLowerCase();

  if (['connected', 'healthy', 'active'].includes(normalized)) return 'statusPill statusPill-success';
  if (['degraded', 'expiring soon', 'pending verification', 'never used'].includes(normalized)) return 'statusPill statusPill-warning';
  if (['error', 'offline', 'failing', 'revoked', 'disconnected'].includes(normalized)) return 'statusPill statusPill-danger';

  return 'statusPill';
}

function StatusBadge({ status }: { status: string }) {
  return <span className={statusClass(status)}>{status}</span>;
}

function MetricCard({ label, value, meta }: { label: string; value: ReactNode; meta?: ReactNode }) {
  return (
    <article className="dataCard">
      <p className="sectionEyebrow">{label}</p>
      <h3 style={{ margin: '0.25rem 0', fontSize: '1.55rem' }}>{value}</h3>
      {meta ? <p className="muted">{meta}</p> : null}
    </article>
  );
}

function DetailRow({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div style={{ display: 'grid', gap: '0.2rem' }}>
      <span className="sectionEyebrow">{label}</span>
      <span>{value}</span>
    </div>
  );
}

function DetailPanel({ title, children }: { title: string; children: ReactNode }) {
  return (
    <aside className="dataCard" style={{ minWidth: 280 }}>
      <p className="sectionEyebrow">{title}</p>
      <div style={{ display: 'grid', gap: '0.75rem' }}>{children}</div>
    </aside>
  );
}

function EmptyState({
  title,
  message,
  action,
  href,
  disabled,
}: {
  title: string;
  message: string;
  action?: string;
  href?: string;
  disabled?: boolean;
}) {
  return (
    <article className="dataCard" style={{ padding: '1.25rem' }}>
      <h3 style={{ marginTop: 0 }}>{title}</h3>
      <p className="muted">{message}</p>
      {action && href ? (
        <Link className="btn btn-secondary" href={href} prefetch={false}>
          {action}
        </Link>
      ) : action ? (
        <button className="btn btn-secondary" type="button" disabled={disabled}>
          {action}
        </button>
      ) : null}
    </article>
  );
}

function DataTable({ headers, children }: { headers: readonly string[]; children: ReactNode }) {
  return (
    <article className="dataCard" style={{ overflowX: 'auto' }}>
      <table>
        <thead>
          <tr>
            {headers.map((header) => (
              <th key={header}>{header}</th>
            ))}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </article>
  );
}

export default function IntegrationsPageClient({ apiUrl }: { apiUrl: string }) {
  const { authHeaders, user } = usePilotAuth();
  const [activeTab, setActiveTab] = useState<TabKey>('providers');
  const [health, setHealth] = useState<IntegrationHealth | null>(null);
  const [monitoringHealth, setMonitoringHealth] = useState<HealthRecord | null>(null);
  const [apiKeys, setApiKeys] = useState<ApiKeyRow[]>([]);
  const [webhooks, setWebhooks] = useState<WebhookRow[]>([]);
  const [slackIntegrations, setSlackIntegrations] = useState<Array<Record<string, unknown>>>([]);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState<string>('');

  const role = String((user as any)?.role ?? (user as any)?.memberships?.[0]?.role ?? '');
  const canManageApiKeys = ['owner', 'admin', 'workspace_owner', 'workspace_admin'].includes(role);

  useEffect(() => {
    let cancelled = false;

    async function loadData() {
      setLoading(true);
      setMessage('');

      try {
        const [healthResponse, monitoringResponse, webhooksResponse, slackResponse, keysResponse] = await Promise.all([
          fetch(apiUrl + '/system/integrations/health', { headers: authHeaders(), cache: 'no-store' }),
          fetch(apiUrl + '/ops/monitoring/health', { headers: authHeaders(), cache: 'no-store' }),
          fetch(apiUrl + '/integrations/webhooks', { headers: authHeaders(), cache: 'no-store' }),
          fetch(apiUrl + '/integrations/slack', { headers: authHeaders(), cache: 'no-store' }),
          canManageApiKeys
            ? fetch('/api/workspace/api-keys', { headers: authHeaders(), cache: 'no-store' })
            : Promise.resolve(null),
        ]);

        if (cancelled) return;

        setHealth(healthResponse.ok ? await healthResponse.json() : null);
        setMonitoringHealth(monitoringResponse.ok ? await monitoringResponse.json() : null);

        const webhookPayload = webhooksResponse.ok ? await webhooksResponse.json() : {};
        const rawWebhooks: Array<Record<string, unknown>> = Array.isArray(webhookPayload.webhooks)
          ? webhookPayload.webhooks
          : [];

        setWebhooks(
          rawWebhooks.map((webhook) => {
            const total = Number(webhook.total_count ?? webhook.delivery_count ?? 0);
            const failed = Number(webhook.failure_count ?? webhook.failed_count ?? 0);

            return {
              id: String(webhook.id ?? webhook.target_url ?? Math.random()),
              webhook: String(webhook.description ?? maskUrl(String(webhook.target_url ?? 'Webhook'))),
              eventTypes: Array.isArray(webhook.event_types) ? (webhook.event_types as string[]) : ['alert', 'incident'],
              status: webhookStatusFromBackend(webhook),
              lastDelivery: String(webhook.last_delivery_at ?? webhook.last_delivered_at ?? '') || null,
              lastError: String(webhook.last_error ?? webhook.error_message ?? '') || null,
              failureRate: total > 0 ? String(Math.round((failed / total) * 100)) + '%' : '-',
              signingSecretStatus: webhook.secret_last4 ? 'Configured (...' + String(webhook.secret_last4) + ')' : 'Not configured',
              retryPolicy: String(webhook.retry_policy ?? 'Default retry policy'),
            };
          }),
        );

        const slackPayload = slackResponse.ok ? await slackResponse.json() : {};
        setSlackIntegrations(Array.isArray(slackPayload.integrations) ? slackPayload.integrations : []);

        if (keysResponse?.ok) {
          const keysPayload = await keysResponse.json();
          const rawKeys: Array<Record<string, unknown>> = Array.isArray(keysPayload.items) ? keysPayload.items : [];

          setApiKeys(
            rawKeys.map((key) => ({
              id: String(key.id ?? key.secret_prefix ?? Math.random()),
              keyName: String(key.label ?? key.name ?? 'Workspace API key'),
              keyPrefix: key.secret_prefix ? String(key.secret_prefix) : null,
              scope: String(key.scope ?? 'workspace'),
              status: apiKeyStatusFromBackend(key),
              created: String(key.created_at ?? '') || null,
              lastUsed: String(key.last_used_at ?? '') || null,
              rotationStatus: String(key.rotation_status ?? (key.revoked_at ? 'Revoked' : 'Rotation not scheduled')),
              owner: String(key.owner ?? key.created_by ?? 'Workspace'),
              linkedIntegration: key.linked_integration ? String(key.linked_integration) : null,
            })),
          );
        } else {
          setApiKeys([]);
        }
      } catch {
        if (!cancelled) {
          setHealth(null);
          setMonitoringHealth(null);
          setMessage('Integration diagnostics are temporarily unavailable.');
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    void loadData();

    return () => {
      cancelled = true;
    };
  }, [apiUrl, canManageApiKeys]);

  const providers = useMemo<ProviderRow[]>(() => {
    if (!health) return [];

    const lastCycle = monitoringHealth?.last_check_at ?? monitoringHealth?.checked_at ?? monitoringHealth?.last_sync_at ?? null;

    return Object.entries(health).map(([key, record]) => {
      const status = providerStatusFromBackend(record);
      const linkedTargets = key.toLowerCase() === 'slack'
        ? slackIntegrations.filter((integration) => integration.enabled !== false).length
        : 0;

      return {
        id: key,
        provider: titleCase(key),
        type: providerTypeFor(key),
        status,
        lastSync: record?.last_sync_at ?? record?.last_check_at ?? record?.checked_at ?? lastCycle,
        lastError: status === 'Connected' ? null : record?.message ?? null,
        linkedTargets,
        linkedSystems: 0,
        evidenceSourceCapability: ['Blockchain RPC', 'Indexer', 'Oracle', 'Compliance Source', 'Internal Stream'].includes(providerTypeFor(key))
          ? 'Supported'
          : 'Not available',
        authenticationStatus: status === 'Connected' ? 'Authenticated' : 'Provider health unavailable',
        nextAction: status === 'Connected' ? 'View Targets' : 'Test Connection',
      };
    });
  }, [health, monitoringHealth, slackIntegrations]);

  const connections = useMemo<ConnectionRow[]>(() => {
    if (!health) return [];

    const lastCheck = monitoringHealth?.last_check_at ?? monitoringHealth?.checked_at ?? monitoringHealth?.last_sync_at ?? null;

    return Object.entries(health).map(([key, record]) => {
      const status = connectionStatusFromBackend(record, lastCheck);

      return {
        id: 'connection-' + key,
        connection: titleCase(key) + ' Connection',
        source: 'RWA Guard',
        destination: titleCase(key),
        status,
        latency: typeof record?.latency_ms === 'number' ? String(record.latency_ms) + 'ms' : '-',
        lastCheck,
        lastError: status === 'Healthy' ? null : record?.message ?? null,
        healthReason: record?.message ?? (status === 'Unknown' ? 'Provider health unavailable' : 'No active issue reported'),
        linkedProvider: titleCase(key),
        linkedTargetSystem: 'Provider health unavailable',
      };
    });
  }, [health, monitoringHealth]);

  const connectedProviders = providers.filter((provider) => provider.status === 'Connected').length;
  const activeApiKeys = apiKeys.filter((key) => key.status === 'Active').length;
  const activeWebhooks = webhooks.filter((webhook) => webhook.status === 'Active').length;
  const degradedConnections = connections.filter((connection) =>
    ['Degraded', 'Offline', 'Unknown'].includes(connection.status),
  ).length;

  const selectedProvider = providers[0] ?? null;
  const selectedApiKey = apiKeys[0] ?? null;
  const selectedWebhook = webhooks[0] ?? null;
  const selectedConnection = connections[0] ?? null;
  const degradedConnection = connections.find((connection) => ['Degraded', 'Offline'].includes(connection.status));

  return (
    <main className="productPage">
      <RuntimeSummaryPanel />

      <section className="featureSection">
        <div className="sectionHeader">
          <div>
            <p className="eyebrow">External integrations</p>
            <h1>Integrations</h1>
            <p className="muted" style={{ marginTop: '0.35rem', maxWidth: 720 }}>
              Manage providers, API keys, webhooks, and external connections used by monitoring sources.
            </p>
          </div>

          <button
            className="btn btn-primary"
            type="button"
            disabled
            title="Action not configured"
          >
            Add Integration
          </button>
        </div>

        <div className="threeColumnSection" style={{ gridTemplateColumns: 'repeat(4, minmax(0, 1fr))' }}>
          <MetricCard label="Connected Providers" value={loading ? '-' : connectedProviders} meta={String(providers.length) + ' provider records'} />
          <MetricCard label="Active API Keys" value={loading ? '-' : canManageApiKeys ? activeApiKeys : '-'} meta={canManageApiKeys ? String(apiKeys.length) + ' keys visible' : 'API key management not configured'} />
          <MetricCard label="Webhooks" value={loading ? '-' : activeWebhooks} meta={String(webhooks.length) + ' webhook records'} />
          <MetricCard label="Degraded Connections" value={loading ? '-' : degradedConnections} meta={degradedConnections > 0 ? 'Needs attention' : 'No degraded connections'} />
        </div>

        {message ? <p className="statusLine">{message}</p> : null}

        <div className="buttonRow" role="tablist" aria-label="Integrations tabs">
          {TABS.map((tab) => (
            <button
              key={tab.key}
              type="button"
              role="tab"
              aria-selected={activeTab === tab.key}
              className={activeTab === tab.key ? 'btn btn-primary' : 'btn btn-secondary'}
              onClick={() => setActiveTab(tab.key)}
            >
              {tab.label}
            </button>
          ))}
        </div>
      </section>

      {activeTab === 'providers' ? (
        <section className="featureSection">
          <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) 320px', gap: '1rem', alignItems: 'start' }}>
            {providers.length === 0 && !loading ? (
              <EmptyState
                title="No integrations configured"
                message="Connect a provider, API key, or webhook before enabling live monitoring."
                action="Add Integration"
                disabled
              />
            ) : (
              <DataTable headers={PROVIDER_HEADERS}>
                {providers.map((provider) => (
                  <tr key={provider.id}>
                    <td>{provider.provider}</td>
                    <td>{provider.type}</td>
                    <td><StatusBadge status={provider.status} /></td>
                    <td>{formatRelative(provider.lastSync)}</td>
                    <td>{provider.lastError ?? '-'}</td>
                    <td>
                      <div className="buttonRow">
                        <button className="btn btn-secondary" type="button" disabled>Configure</button>
                        <button className="btn btn-secondary" type="button" disabled>Test Connection</button>
                        <Link className="btn btn-secondary" href="/monitoring-sources" prefetch={false}>View Targets</Link>
                      </div>
                    </td>
                  </tr>
                ))}
              </DataTable>
            )}

            <DetailPanel title="Provider detail panel">
              {selectedProvider ? (
                <>
                  <DetailRow label="Provider name" value={selectedProvider.provider} />
                  <DetailRow label="Provider type" value={selectedProvider.type} />
                  <DetailRow label="Status" value={<StatusBadge status={selectedProvider.status} />} />
                  <DetailRow label="Last sync" value={formatDate(selectedProvider.lastSync)} />
                  <DetailRow label="Last error" value={selectedProvider.lastError ?? '-'} />
                  <DetailRow
                    label="Linked monitoring targets"
                    value={
                      selectedProvider.linkedTargets > 0 ? (
                        <Link href="/monitoring-sources" prefetch={false}>
                          {selectedProvider.linkedTargets} linked target{selectedProvider.linkedTargets === 1 ? '' : 's'}
                        </Link>
                      ) : (
                        <span>Provider configured, but no monitoring target is linked</span>
                      )
                    }
                  />
                  <DetailRow label="Linked monitored systems" value={selectedProvider.linkedSystems || '-'} />
                  <DetailRow label="Evidence source capability" value={selectedProvider.evidenceSourceCapability} />
                  <DetailRow label="Authentication status" value={selectedProvider.authenticationStatus} />
                  <DetailRow label="Next required action" value={selectedProvider.nextAction} />
                </>
              ) : (
                <p className="muted">Select a provider to inspect connection details.</p>
              )}
            </DetailPanel>
          </div>
        </section>
      ) : null}

      {activeTab === 'api-keys' ? (
        <section className="featureSection">
          <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) 320px', gap: '1rem', alignItems: 'start' }}>
            {!canManageApiKeys ? (
              <EmptyState
                title="API key management not configured"
                message="API key creation and rotation are not enabled for this workspace yet."
                action="Action not configured"
                disabled
              />
            ) : apiKeys.length === 0 && !loading ? (
              <EmptyState
                title="API key management not configured"
                message="API key creation and rotation are not enabled for this workspace yet."
                action="Action not configured"
                disabled
              />
            ) : (
              <DataTable headers={API_KEY_HEADERS}>
                {apiKeys.map((key) => (
                  <tr key={key.id}>
                    <td>{key.keyName}</td>
                    <td>{key.scope}</td>
                    <td><StatusBadge status={key.status} /></td>
                    <td>{formatDate(key.created)}</td>
                    <td>{formatRelative(key.lastUsed)}</td>
                    <td>
                      <div className="buttonRow">
                        <button className="btn btn-secondary" type="button" disabled>Rotate Key</button>
                        <button className="btn btn-secondary" type="button" disabled>Revoke Key</button>
                        <button className="btn btn-secondary" type="button" disabled>View Usage</button>
                      </div>
                    </td>
                  </tr>
                ))}
              </DataTable>
            )}

            <DetailPanel title="API key detail panel">
              {selectedApiKey ? (
                <>
                  <DetailRow label="Key name" value={selectedApiKey.keyName} />
                  <DetailRow label="Key prefix" value={selectedApiKey.keyPrefix ? selectedApiKey.keyPrefix + "..." : "Not available"} />
                  <DetailRow label="Scope" value={selectedApiKey.scope} />
                  <DetailRow label="Status" value={<StatusBadge status={selectedApiKey.status} />} />
                  <DetailRow label="Created at" value={formatDate(selectedApiKey.created)} />
                  <DetailRow label="Last used" value={formatRelative(selectedApiKey.lastUsed)} />
                  <DetailRow label="Rotation status" value={selectedApiKey.rotationStatus} />
                  <DetailRow label="Owner" value={selectedApiKey.owner} />
                  <DetailRow label="Linked integration" value={selectedApiKey.linkedIntegration ?? '-'} />
                </>
              ) : (
                <p className="muted">API key management not configured</p>
              )}
            </DetailPanel>
          </div>
        </section>
      ) : null}

      {activeTab === 'webhooks' ? (
        <section className="featureSection">
          <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) 320px', gap: '1rem', alignItems: 'start' }}>
            {webhooks.length === 0 && !loading ? (
              <EmptyState
                title="Webhooks not configured"
                message="Webhook delivery is not enabled for this workspace yet."
                action="Action not configured"
                disabled
              />
            ) : (
              <DataTable headers={WEBHOOK_HEADERS}>
                {webhooks.map((webhook) => (
                  <tr key={webhook.id}>
                    <td>{webhook.webhook}</td>
                    <td>{webhook.eventTypes.join(', ')}</td>
                    <td><StatusBadge status={webhook.status} /></td>
                    <td>{formatRelative(webhook.lastDelivery)}</td>
                    <td>{webhook.failureRate}</td>
                    <td>
                      <div className="buttonRow">
                        <button className="btn btn-secondary" type="button" disabled>Configure</button>
                        <button className="btn btn-secondary" type="button" disabled>Test Delivery</button>
                        <button className="btn btn-secondary" type="button" disabled>View Deliveries</button>
                      </div>
                    </td>
                  </tr>
                ))}
              </DataTable>
            )}

            <DetailPanel title="Webhook detail panel">
              {selectedWebhook ? (
                <>
                  <DetailRow label="Webhook URL" value={selectedWebhook.webhook} />
                  <DetailRow label="Event types" value={selectedWebhook.eventTypes.join(', ')} />
                  <DetailRow label="Status" value={<StatusBadge status={selectedWebhook.status} />} />
                  <DetailRow label="Last delivery" value={formatDate(selectedWebhook.lastDelivery)} />
                  <DetailRow label="Last error" value={selectedWebhook.lastError ?? '-'} />
                  <DetailRow label="Signing secret status" value={selectedWebhook.signingSecretStatus} />
                  <DetailRow label="Retry policy" value={selectedWebhook.retryPolicy} />
                  <DetailRow label="Delivery history" value="View deliveries action not configured" />
                </>
              ) : (
                <p className="muted">Webhooks not configured</p>
              )}
            </DetailPanel>
          </div>
        </section>
      ) : null}

      {activeTab === 'connections' ? (
        <section className="featureSection">
          <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) 320px', gap: '1rem', alignItems: 'start' }}>
            {connections.length === 0 && !loading ? (
              <EmptyState
                title="No connections configured"
                message="Connections appear when provider health checks are available."
                action="View System Health"
                href="/system-health"
              />
            ) : (
              <DataTable headers={CONNECTION_HEADERS}>
                {connections.map((connection) => (
                  <tr key={connection.id}>
                    <td>{connection.connection}</td>
                    <td>{connection.source}</td>
                    <td>{connection.destination}</td>
                    <td><StatusBadge status={connection.status} /></td>
                    <td>{connection.latency}</td>
                    <td>{formatRelative(connection.lastCheck)}</td>
                    <td>
                      <div className="buttonRow">
                        <button className="btn btn-secondary" type="button" disabled>Test Connection</button>
                        <Link className="btn btn-secondary" href="/system-health" prefetch={false}>View System Health</Link>
                      </div>
                    </td>
                  </tr>
                ))}
              </DataTable>
            )}

            <DetailPanel title="Connection detail panel">
              {selectedConnection ? (
                <>
                  <DetailRow label="Connection name" value={selectedConnection.connection} />
                  <DetailRow label="Source" value={selectedConnection.source} />
                  <DetailRow label="Destination" value={selectedConnection.destination} />
                  <DetailRow label="Status" value={<StatusBadge status={selectedConnection.status} />} />
                  <DetailRow label="Latency" value={selectedConnection.latency} />
                  <DetailRow label="Last check" value={formatDate(selectedConnection.lastCheck)} />
                  <DetailRow label="Last error" value={selectedConnection.lastError ?? '-'} />
                  <DetailRow label="Health reason" value={selectedConnection.healthReason} />
                  <DetailRow label="Linked provider" value={selectedConnection.linkedProvider} />
                  <DetailRow
                    label="Linked target/system"
                    value={<Link href="/monitoring-sources" prefetch={false}>{selectedConnection.linkedTargetSystem}</Link>}
                  />
                </>
              ) : (
                <p className="muted">Provider health unavailable</p>
              )}
            </DetailPanel>
          </div>

          {degradedConnection ? (
            <article className="dataCard" style={{ marginTop: '1rem' }}>
              <h3>Connection degraded</h3>
              <p className="muted">{degradedConnection.healthReason}</p>
              <Link className="btn btn-secondary" href="/system-health" prefetch={false}>View System Health</Link>
            </article>
          ) : null}
        </section>
      ) : null}
    </main>
  );
}





