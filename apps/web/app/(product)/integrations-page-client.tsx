'use client';

import { useEffect, useMemo, useState } from 'react';

import { usePilotAuth } from '../pilot-auth-context';
import RuntimeSummaryPanel from '../runtime-summary-panel';

type RoutingRule = {
  channel_type: 'dashboard' | 'email' | 'webhook' | 'slack';
  severity_threshold: 'low' | 'medium' | 'high' | 'critical';
  enabled: boolean;
};

type IntegrationRecord = Record<string, any>;
type TabKey = 'providers' | 'api-keys' | 'webhooks' | 'connections';

const CHANNELS: Array<RoutingRule['channel_type']> = ['dashboard', 'email', 'webhook', 'slack'];
const TABS: Array<{ key: TabKey; label: string }> = [
  { key: 'providers', label: 'Providers' },
  { key: 'api-keys', label: 'API Keys' },
  { key: 'webhooks', label: 'Webhooks' },
  { key: 'connections', label: 'Connections' },
];

export default function IntegrationsPageClient({ apiUrl }: { apiUrl: string }) {
  const { authHeaders, user } = usePilotAuth();
  const [activeTab, setActiveTab] = useState<TabKey>('providers');

  const [webhooks, setWebhooks] = useState<IntegrationRecord[]>([]);
  const [slackIntegrations, setSlackIntegrations] = useState<IntegrationRecord[]>([]);
  const [slackDeliveries, setSlackDeliveries] = useState<IntegrationRecord[]>([]);
  const [webhookDeliveries, setWebhookDeliveries] = useState<IntegrationRecord[]>([]);
  const [routingRules, setRoutingRules] = useState<RoutingRule[]>([]);
  const [health, setHealth] = useState<any>(null);
  const [workerHealth, setWorkerHealth] = useState<any>(null);
  const [apiKeys, setApiKeys] = useState<Array<{ id: string; label: string; secret_prefix: string; revoked_at: string | null }>>([]);

  const [targetUrl, setTargetUrl] = useState('');
  const [description, setDescription] = useState('');
  const [activeWebhookId, setActiveWebhookId] = useState<string | null>(null);
  const [apiKeyLabel, setApiKeyLabel] = useState('');
  const [revealedSecret, setRevealedSecret] = useState('');
  const [message, setMessage] = useState('');
  const [saving, setSaving] = useState(false);

  const canManageApiKeys = ['owner', 'admin', 'workspace_owner', 'workspace_admin'].includes(String((user as any)?.role ?? user?.memberships?.[0]?.role ?? ''));

  const selectedWebhook = useMemo(() => webhooks.find((item) => item.id === activeWebhookId) ?? webhooks[0] ?? null, [activeWebhookId, webhooks]);
  const deliveryFailures = useMemo(() => slackDeliveries.filter((item) => item.status !== 'succeeded').length, [slackDeliveries]);
  const ruleFor = (channel: RoutingRule['channel_type']) => routingRules.find((rule) => rule.channel_type === channel) ?? { channel_type: channel, severity_threshold: 'medium', enabled: true };

  async function loadApiKeys() {
    const response = await fetch('/api/workspace/api-keys', { headers: authHeaders() });
    if (!response.ok) return;
    const payload = await response.json();
    setApiKeys(Array.isArray(payload.items) ? payload.items : []);
  }

  async function loadAll() {
    const [webhookResponse, slackResponse, routingResponse, healthResponse] = await Promise.all([
      fetch(`${apiUrl}/integrations/webhooks`, { headers: authHeaders(), cache: 'no-store' }),
      fetch(`${apiUrl}/integrations/slack`, { headers: authHeaders(), cache: 'no-store' }),
      fetch(`${apiUrl}/integrations/routing`, { headers: authHeaders(), cache: 'no-store' }),
      fetch(`${apiUrl}/system/integrations/health`, { headers: authHeaders(), cache: 'no-store' }),
    ]);
    const nextWebhooks = webhookResponse.ok ? ((await webhookResponse.json()).webhooks ?? []) : [];
    const nextSlack = slackResponse.ok ? ((await slackResponse.json()).integrations ?? []) : [];
    const nextRules = routingResponse.ok ? ((await routingResponse.json()).rules ?? []) : [];
    setWebhooks(nextWebhooks);
    setSlackIntegrations(nextSlack);
    setRoutingRules(nextRules);
    setHealth(healthResponse.ok ? await healthResponse.json() : null);
    const monitoringHealthResponse = await fetch(`${apiUrl}/ops/monitoring/health`, { headers: authHeaders(), cache: 'no-store' });
    setWorkerHealth(monitoringHealthResponse.ok ? await monitoringHealthResponse.json() : null);

    const nextWebhookId = activeWebhookId && nextWebhooks.some((item: IntegrationRecord) => item.id === activeWebhookId) ? activeWebhookId : nextWebhooks[0]?.id;
    if (nextWebhookId) {
      setActiveWebhookId(nextWebhookId);
      const webhookDeliveriesResponse = await fetch(`${apiUrl}/integrations/webhooks/${nextWebhookId}/deliveries`, { headers: authHeaders(), cache: 'no-store' });
      setWebhookDeliveries(webhookDeliveriesResponse.ok ? ((await webhookDeliveriesResponse.json()).deliveries ?? []) : []);
    } else {
      setWebhookDeliveries([]);
    }

    if (canManageApiKeys) await loadApiKeys();

    if (nextSlack[0]?.id) {
      const logResponse = await fetch(`${apiUrl}/integrations/slack/${nextSlack[0].id}/deliveries`, { headers: authHeaders(), cache: 'no-store' });
      setSlackDeliveries(logResponse.ok ? ((await logResponse.json()).deliveries ?? []) : []);
    } else {
      setSlackDeliveries([]);
    }
  }

  useEffect(() => { void loadAll(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  async function createApiKey() {
    setSaving(true);
    const response = await fetch('/api/workspace/api-keys', { method: 'POST', headers: { ...authHeaders(), 'Content-Type': 'application/json' }, body: JSON.stringify({ label: apiKeyLabel }) });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) setMessage(payload.detail ?? 'Unable to create API key.');
    else {
      setRevealedSecret(payload.secret ?? '');
      setApiKeyLabel('');
      setMessage('API key created. Secret is shown once—copy it now.');
      await loadApiKeys();
    }
    setSaving(false);
  }

  async function createWebhook() { /* unchanged behavior */
    if (!targetUrl.trim().startsWith('https://')) return setMessage('Webhook URL must start with https:// for production-safe delivery.');
    setSaving(true);
    const response = await fetch(`${apiUrl}/integrations/webhooks`, { method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() }, body: JSON.stringify({ target_url: targetUrl, description }) });
    const payload = await response.json().catch(() => ({}));
    if (response.ok) { setMessage(`Webhook saved. Secret (shown once): ${payload.secret}`); setTargetUrl(''); setDescription(''); void loadAll(); }
    else setMessage(payload.detail || 'Unable to create webhook.');
    setSaving(false);
  }

  async function toggleWebhook(webhook: any) { await fetch(`${apiUrl}/integrations/webhooks/${webhook.id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json', ...authHeaders() }, body: JSON.stringify({ enabled: !webhook.enabled, description: webhook.description || '' }) }); await loadAll(); }
  async function rotateWebhookSecret(webhook: any) { const response = await fetch(`${apiUrl}/integrations/webhooks/${webhook.id}/rotate-secret`, { method: 'POST', headers: authHeaders() }); const payload = await response.json().catch(() => ({})); setMessage(response.ok ? `Webhook secret rotated. New secret (shown once): ${payload.secret}` : (payload.detail || 'Unable to rotate webhook secret.')); await loadAll(); }
  async function updateRouting(channel: RoutingRule['channel_type'], threshold: RoutingRule['severity_threshold'], enabled: boolean) { const response = await fetch(`${apiUrl}/integrations/routing/${channel}`, { method: 'PUT', headers: { 'Content-Type': 'application/json', ...authHeaders() }, body: JSON.stringify({ severity_threshold: threshold, enabled }) }); if (response.ok) await loadAll(); }

  return <main className="productPage">
    <RuntimeSummaryPanel />
    <section className="featureSection"><div className="sectionHeader"><div><p className="eyebrow">System readiness</p><h1>Integrations health and delivery</h1></div></div>
      <article className="dataCard"><p className="muted">Stripe: {health?.stripe?.status || 'unknown'} · {health?.stripe?.message || 'No diagnostics yet.'}</p>
        <p className="muted">Email: {health?.email?.status || 'unknown'} · {health?.email?.message || 'No diagnostics yet.'}</p>
        <p className="muted">Slack: {health?.slack?.status || 'unknown'} · Active channels: {slackIntegrations.filter((item) => item.enabled).length}</p>
        <p className="muted">Recent Slack failures: {deliveryFailures}</p>
        <p className="muted">Background worker: {workerHealth?.last_cycle_at ? `last cycle ${new Date(workerHealth.last_cycle_at).toLocaleString()}` : 'no recent cycle detected'}</p>
        {message ? <p className="statusLine">{message}</p> : null}
      </article>
    </section>

    <section className="featureSection">
      <div className="buttonRow" role="tablist" aria-label="Integration tabs">
        {TABS.map((tab) => <button key={tab.key} type="button" role="tab" aria-selected={activeTab === tab.key} onClick={() => setActiveTab(tab.key)}>{tab.label}</button>)}
      </div>

      {activeTab === 'providers' ? <article className="dataCard">
        <p className="sectionEyebrow">Providers</p>
        <table><thead><tr><th>Provider</th><th>Type</th><th>Status</th><th>Last Sync</th><th>Last Error</th><th>Action</th></tr></thead>
          <tbody>
            <tr><td>Stripe</td><td>Billing</td><td>{health?.stripe?.status || 'unknown'}</td><td>{workerHealth?.last_cycle_at ? new Date(workerHealth.last_cycle_at).toLocaleString() : '—'}</td><td>{health?.stripe?.message || '—'}</td><td><button type="button" onClick={() => setActiveTab('connections')}>View connections</button></td></tr>
            <tr><td>Email</td><td>Notification</td><td>{health?.email?.status || 'unknown'}</td><td>{workerHealth?.last_cycle_at ? new Date(workerHealth.last_cycle_at).toLocaleString() : '—'}</td><td>{health?.email?.message || '—'}</td><td><button type="button" onClick={() => setActiveTab('connections')}>View connections</button></td></tr>
            <tr><td>Slack</td><td>Notification</td><td>{health?.slack?.status || 'unknown'}</td><td>{slackDeliveries[0]?.created_at ? new Date(slackDeliveries[0].created_at).toLocaleString() : '—'}</td><td>{slackDeliveries.find((item) => item.error_message)?.error_message || '—'}</td><td><button type="button" onClick={() => setActiveTab('connections')}>Manage</button></td></tr>
          </tbody></table>
      </article> : null}

      {activeTab === 'api-keys' ? <article className="dataCard"><p className="sectionEyebrow">API keys</p>
        {canManageApiKeys ? <>
          <div className="buttonRow"><input placeholder="Key label" value={apiKeyLabel} onChange={(event) => setApiKeyLabel(event.target.value)} /><button type="button" onClick={() => void createApiKey()} disabled={saving || apiKeyLabel.trim().length < 2}>Create key</button><button type="button" onClick={() => void loadApiKeys()} disabled={saving}>Refresh</button></div>
          {revealedSecret ? <pre>{revealedSecret}</pre> : null}
          <ul>{apiKeys.map((key) => <li key={key.id}><code>{key.secret_prefix}…</code> {key.label} · status: {key.revoked_at ? 'revoked' : 'active'}
            <button type="button" onClick={async () => { await fetch(`/api/workspace/api-keys/${key.id}/rotate`, { method: 'POST', headers: authHeaders() }); await loadApiKeys(); }}>Rotate</button></li>)}</ul>
        </> : <p className="muted">Owner or admin role is required to manage workspace API keys.</p>}
      </article> : null}

      {activeTab === 'webhooks' ? <article className="dataCard"><p className="sectionEyebrow">Webhooks</p><input placeholder="https://example.com/webhooks/decoda" value={targetUrl} onChange={(event) => setTargetUrl(event.target.value)} /><input placeholder="Description" value={description} onChange={(event) => setDescription(event.target.value)} /><button type="button" onClick={() => void createWebhook()} disabled={saving}>Create webhook</button>{webhooks.length === 0 ? <p className="muted">No webhooks configured yet.</p> : webhooks.map((webhook) => <div key={webhook.id} style={{ marginTop: 12 }}><p>{webhook.description || 'Webhook'} · {webhook.enabled ? 'enabled' : 'disabled'} · secret last4 {webhook.secret_last4 || '----'}</p><p className="muted">{webhook.target_url}</p><div className="buttonRow"><button type="button" onClick={() => { setActiveWebhookId(webhook.id); void toggleWebhook(webhook); }}>{webhook.enabled ? 'Disable' : 'Enable'}</button><button type="button" onClick={() => { setActiveWebhookId(webhook.id); void rotateWebhookSecret(webhook); }}>Rotate secret</button></div></div>)}<p className="muted">Recent webhook deliveries: {selectedWebhook ? webhookDeliveries.length : 0}</p></article> : null}

      {activeTab === 'connections' ? <div className="threeColumnSection">
        <article className="dataCard"><p className="sectionEyebrow">Slack connection</p><p className="muted">Status: {health?.slack?.status || 'unknown'}</p><p className="muted">Enabled channels: {slackIntegrations.filter((item) => item.enabled).length}</p></article>
        <article className="dataCard"><p className="sectionEyebrow">Email connection</p><p className="muted">Status: {health?.email?.status || 'unknown'}</p><p className="muted">Details: {health?.email?.message || 'No diagnostics yet.'}</p></article>
        <article className="dataCard"><p className="sectionEyebrow">Provider health routing</p>{CHANNELS.map((channel) => { const rule = ruleFor(channel); return <div key={channel} className="buttonRow"><span>{channel}</span><select value={rule.severity_threshold} onChange={(event) => void updateRouting(channel, event.target.value as RoutingRule['severity_threshold'], rule.enabled)}><option value="low">low</option><option value="medium">medium</option><option value="high">high</option><option value="critical">critical</option></select><button type="button" onClick={() => void updateRouting(channel, rule.severity_threshold, !rule.enabled)}>{rule.enabled ? 'Disable' : 'Enable'}</button></div>; })}</article>
      </div> : null}
    </section>
  </main>;
}
