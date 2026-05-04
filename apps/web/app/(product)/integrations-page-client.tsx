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

const CHANNELS: Array<RoutingRule['channel_type']> = ['dashboard', 'email', 'webhook', 'slack'];

export default function IntegrationsPageClient({ apiUrl }: { apiUrl: string }) {
  const { authHeaders } = usePilotAuth();
  const [webhooks, setWebhooks] = useState<IntegrationRecord[]>([]);
  const [slackIntegrations, setSlackIntegrations] = useState<IntegrationRecord[]>([]);
  const [slackDeliveries, setSlackDeliveries] = useState<IntegrationRecord[]>([]);
  const [webhookDeliveries, setWebhookDeliveries] = useState<IntegrationRecord[]>([]);
  const [routingRules, setRoutingRules] = useState<RoutingRule[]>([]);
  const [health, setHealth] = useState<any>(null);
  const [workerHealth, setWorkerHealth] = useState<any>(null);

  const [targetUrl, setTargetUrl] = useState('');
  const [description, setDescription] = useState('');
  const [activeWebhookId, setActiveWebhookId] = useState<string | null>(null);
  const [slackName, setSlackName] = useState('Incident room');
  const [slackMode, setSlackMode] = useState<'webhook' | 'bot'>('webhook');
  const [slackWebhookUrl, setSlackWebhookUrl] = useState('');
  const [slackBotToken, setSlackBotToken] = useState('');
  const [slackChannel, setSlackChannel] = useState('');
  const [activeSlackId, setActiveSlackId] = useState<string | null>(null);
  const [message, setMessage] = useState('');
  const [saving, setSaving] = useState(false);

  const selectedSlack = useMemo(
    () => slackIntegrations.find((item) => item.id === activeSlackId) ?? slackIntegrations[0] ?? null,
    [activeSlackId, slackIntegrations],
  );
  const selectedWebhook = useMemo(
    () => webhooks.find((item) => item.id === activeWebhookId) ?? webhooks[0] ?? null,
    [activeWebhookId, webhooks],
  );

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

    const nextSlackId = activeSlackId && nextSlack.some((item: IntegrationRecord) => item.id === activeSlackId) ? activeSlackId : nextSlack[0]?.id;
    if (nextSlackId) {
      setActiveSlackId(nextSlackId);
      const logResponse = await fetch(`${apiUrl}/integrations/slack/${nextSlackId}/deliveries`, { headers: authHeaders(), cache: 'no-store' });
      setSlackDeliveries(logResponse.ok ? ((await logResponse.json()).deliveries ?? []) : []);
    } else {
      setSlackDeliveries([]);
    }
    const nextWebhookId = activeWebhookId && nextWebhooks.some((item: IntegrationRecord) => item.id === activeWebhookId) ? activeWebhookId : nextWebhooks[0]?.id;
    if (nextWebhookId) {
      setActiveWebhookId(nextWebhookId);
      const webhookDeliveriesResponse = await fetch(`${apiUrl}/integrations/webhooks/${nextWebhookId}/deliveries`, { headers: authHeaders(), cache: 'no-store' });
      setWebhookDeliveries(webhookDeliveriesResponse.ok ? ((await webhookDeliveriesResponse.json()).deliveries ?? []) : []);
    } else {
      setWebhookDeliveries([]);
    }
  }

  useEffect(() => { void loadAll(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  function validateSlackInput() {
    if (!slackName.trim()) {
      return 'Display name is required for Slack integrations.';
    }
    if (slackMode === 'webhook' && !/^https:\/\/hooks\.slack\.com\/services\//.test(slackWebhookUrl.trim())) {
      return 'Webhook mode requires a valid Slack incoming webhook URL.';
    }
    if (slackMode === 'bot' && !slackBotToken.trim().startsWith('xoxb-')) {
      return 'Bot mode requires a valid xoxb token.';
    }
    return null;
  }

  async function createSlackIntegration() {
    const validationError = validateSlackInput();
    if (validationError) {
      setMessage(validationError);
      return;
    }
    setSaving(true);
    const response = await fetch(`${apiUrl}/integrations/slack`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({
        display_name: slackName,
        mode: slackMode,
        webhook_url: slackMode === 'webhook' ? slackWebhookUrl : undefined,
        bot_token: slackMode === 'bot' ? slackBotToken : undefined,
        default_channel: slackChannel || undefined,
      }),
    });
    const payload = response.ok ? null : await response.json().catch(() => null);
    setMessage(response.ok ? `Slack (${slackMode}) integration saved.` : (payload?.detail || 'Unable to create Slack integration.'));
    if (response.ok) {
      setSlackWebhookUrl('');
      setSlackBotToken('');
      setSlackChannel('');
      void loadAll();
    }
    setSaving(false);
  }

  async function startSlackOAuthInstall() {
    setSaving(true);
    const response = await fetch(`${apiUrl}/integrations/slack/oauth/start`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ redirect_after_install: '/integrations' }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || !payload?.authorize_url) {
      setMessage(payload?.detail || 'Slack OAuth install is unavailable. Confirm Slack OAuth environment variables.');
      setSaving(false);
      return;
    }
    window.location.assign(payload.authorize_url);
  }

  async function testSlack(item: any) {
    const response = await fetch(`${apiUrl}/integrations/slack/${item.id}/test`, { method: 'POST', headers: authHeaders() });
    setMessage(response.ok ? `Slack test queued (${item.slack_mode || 'webhook'}).` : 'Slack test failed to queue.');
    await loadAll();
  }

  async function runEmailTest() {
    const response = await fetch(`${apiUrl}/system/integrations/test-email`, { method: 'POST', headers: authHeaders() });
    setMessage(response.ok ? 'Test email sent to your admin account.' : 'Test email failed. Check email provider configuration.');
  }

  async function runSlackHealthTest() {
    if (!selectedSlack) {
      setMessage('No Slack channel configured yet. Add one before running a test.');
      return;
    }
    const response = await fetch(`${apiUrl}/system/integrations/test-slack`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ integration_id: selectedSlack.id })
    });
    setMessage(response.ok ? 'Slack health test queued.' : 'Slack health test failed.');
  }

  async function createWebhook() {
    if (!targetUrl.trim().startsWith('https://')) {
      setMessage('Webhook URL must start with https:// for production-safe delivery.');
      return;
    }
    setSaving(true);
    const response = await fetch(`${apiUrl}/integrations/webhooks`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ target_url: targetUrl, description }),
    });
    const payload = await response.json().catch(() => ({}));
    if (response.ok) {
      setMessage(`Webhook saved. Secret (shown once): ${payload.secret}`);
      setTargetUrl('');
      setDescription('');
      void loadAll();
    } else {
      setMessage(payload.detail || 'Unable to create webhook.');
    }
    setSaving(false);
  }

  async function toggleWebhook(webhook: any) {
    const response = await fetch(`${apiUrl}/integrations/webhooks/${webhook.id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ enabled: !webhook.enabled, description: webhook.description || '' }),
    });
    setMessage(response.ok ? `Webhook ${!webhook.enabled ? 'enabled' : 'disabled'}.` : 'Unable to update webhook state.');
    await loadAll();
  }

  async function rotateWebhookSecret(webhook: any) {
    const response = await fetch(`${apiUrl}/integrations/webhooks/${webhook.id}/rotate-secret`, {
      method: 'POST',
      headers: authHeaders(),
    });
    const payload = await response.json().catch(() => ({}));
    setMessage(response.ok ? `Webhook secret rotated. New secret (shown once): ${payload.secret}` : (payload.detail || 'Unable to rotate webhook secret.'));
    await loadAll();
  }

  async function updateSlackIntegration(integration: any, enabled: boolean) {
    const response = await fetch(`${apiUrl}/integrations/slack/${integration.id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ enabled }),
    });
    setMessage(response.ok ? `Slack integration ${enabled ? 'enabled' : 'disabled'}.` : 'Unable to update Slack integration.');
    await loadAll();
  }

  async function deleteSlackIntegration(integration: any) {
    const response = await fetch(`${apiUrl}/integrations/slack/${integration.id}`, {
      method: 'DELETE',
      headers: authHeaders(),
    });
    setMessage(response.ok ? 'Slack integration removed.' : 'Unable to remove Slack integration.');
    await loadAll();
  }

  const deliveryFailures = useMemo(() => slackDeliveries.filter((item) => item.status !== 'succeeded').length, [slackDeliveries]);
  const ruleFor = (channel: RoutingRule['channel_type']) => routingRules.find((rule) => rule.channel_type === channel) ?? { channel_type: channel, severity_threshold: 'medium', enabled: true };

  async function updateRouting(channel: RoutingRule['channel_type'], threshold: RoutingRule['severity_threshold'], enabled: boolean) {
    const response = await fetch(`${apiUrl}/integrations/routing/${channel}`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json', ...authHeaders() }, body: JSON.stringify({ severity_threshold: threshold, enabled })
    });
    if (response.ok) await loadAll();
  }

  return <main className="productPage">
      <RuntimeSummaryPanel />
    <section className="featureSection"><div className="sectionHeader"><div><p className="eyebrow">System readiness</p><h1>Integrations health and delivery</h1><p className="lede">See Stripe, email, and Slack readiness before customers depend on production alerts.</p></div></div>
      <div className="threeColumnSection">
        <article className="dataCard"><p className="sectionEyebrow">Health summary</p>
          <p className="muted">Stripe: {health?.stripe?.status || 'unknown'} · {health?.stripe?.message || 'No diagnostics yet.'}</p>
          <p className="muted">Email: {health?.email?.status || 'unknown'} · {health?.email?.message || 'No diagnostics yet.'}</p>
          <p className="muted">Slack: {health?.slack?.status || 'unknown'} · Active channels: {slackIntegrations.filter((item) => item.enabled).length}</p>
          <p className="muted">Auth rate limiting: {health?.auth_rate_limiter?.status || 'unknown'} · {health?.auth_rate_limiter?.message || 'No diagnostics yet.'}</p>
          <p className="muted">Recent Slack failures: {deliveryFailures}</p>
          <p className="muted">Background worker: {workerHealth?.last_cycle_at ? `healthy (last cycle ${new Date(workerHealth.last_cycle_at).toLocaleString()})` : 'no recent cycle detected'}</p>
          {!workerHealth?.last_cycle_at ? <p className="statusLine">Background worker has not reported a recent cycle. Test sends may stay queued until worker processing is active.</p> : null}
          {message ? <p className="statusLine">{message}</p> : null}
        </article>
        <article className="dataCard"><p className="sectionEyebrow">Safe test actions</p><button type="button" onClick={() => void runEmailTest()}>Test email delivery</button><button type="button" onClick={() => void runSlackHealthTest()}>Test Slack delivery</button><p className="muted">Billing is not fully configured if Stripe checks report warnings.</p></article>
        <article className="dataCard"><p className="sectionEyebrow">Create Slack integration</p>
          <p className="muted">Use one-click OAuth for self-serve setup, or manual webhook/bot token setup if your Slack app policy requires it.</p>
          <button type="button" onClick={() => void startSlackOAuthInstall()} disabled={saving}>Connect with Slack OAuth</button>
          <input placeholder="Display name" value={slackName} onChange={(event) => setSlackName(event.target.value)} />
          <select value={slackMode} onChange={(event) => setSlackMode(event.target.value as 'webhook' | 'bot')}><option value="webhook">Incoming webhook (compatibility)</option><option value="bot">Bot token (recommended)</option></select>
          {slackMode === 'webhook' ? <input placeholder="https://hooks.slack.com/services/..." value={slackWebhookUrl} onChange={(event) => setSlackWebhookUrl(event.target.value)} /> : <input placeholder="xoxb-..." value={slackBotToken} onChange={(event) => setSlackBotToken(event.target.value)} />}
          <input placeholder="#alerts or C012345" value={slackChannel} onChange={(event) => setSlackChannel(event.target.value)} />
          <button type="button" onClick={() => void createSlackIntegration()} disabled={saving}>Save Slack integration</button>
        </article>
      </div>
    </section>

    <section className="featureSection"><div className="sectionHeader"><div><p className="eyebrow">Routing</p><h2>Per-channel thresholds</h2></div></div><div className="threeColumnSection">{CHANNELS.map((channel) => { const rule = ruleFor(channel); return <article key={channel} className="dataCard"><p className="sectionEyebrow">{channel}</p><div className="buttonRow"><select value={rule.severity_threshold} onChange={(event) => void updateRouting(channel, event.target.value as RoutingRule['severity_threshold'], rule.enabled)}><option value="low">low</option><option value="medium">medium</option><option value="high">high</option><option value="critical">critical</option></select><button type="button" onClick={() => void updateRouting(channel, rule.severity_threshold, !rule.enabled)}>{rule.enabled ? 'Disable' : 'Enable'}</button></div></article>; })}</div></section>

    <section className="featureSection"><div className="sectionHeader"><div><p className="eyebrow">Configured Slack channels</p><h2>Mode-aware management</h2></div></div><div className="threeColumnSection"><article className="dataCard"><p className="sectionEyebrow">Slack</p>{slackIntegrations.length === 0 ? <p className="muted">No Slack channel configured yet.</p> : slackIntegrations.map((item) => <div key={item.id} style={{ marginBottom: 10 }}><p>{item.display_name} · {item.enabled ? 'enabled' : 'disabled'} · mode {item.slack_mode || 'webhook'} · secret last4 {(item.bot_token_last4 || item.webhook_last4) || '----'}</p><div className="buttonRow"><button type="button" onClick={() => { setActiveSlackId(item.id); void testSlack(item); }}>Test send</button><button type="button" onClick={() => void updateSlackIntegration(item, !item.enabled)}>{item.enabled ? 'Disable' : 'Enable'}</button><button type="button" onClick={() => void deleteSlackIntegration(item)}>Delete</button></div></div>)}</article><article className="dataCard"><p className="sectionEyebrow">Recent Slack deliveries</p>{slackDeliveries.length === 0 ? <p className="muted">No Slack deliveries yet.</p> : slackDeliveries.slice(0, 10).map((delivery) => <p key={delivery.id}>{delivery.event_type} · {delivery.provider_mode || 'webhook'} · {delivery.status} · HTTP {delivery.response_status ?? '-'} {delivery.error_message ? `· ${delivery.error_message}` : ''}</p>)}<p className="muted">Retry guidance: fix credentials/channel config first, then run “Test send” again to queue a new delivery.</p></article><article className="dataCard"><p className="sectionEyebrow">Webhooks</p><input placeholder="https://example.com/webhooks/decoda" value={targetUrl} onChange={(event) => setTargetUrl(event.target.value)} /><input placeholder="Description" value={description} onChange={(event) => setDescription(event.target.value)} /><button type="button" onClick={() => void createWebhook()} disabled={saving}>Create webhook</button>{webhooks.length === 0 ? <p className="muted">No webhooks configured yet.</p> : webhooks.map((webhook) => <div key={webhook.id} style={{ marginTop: 12 }}><p>{webhook.description || 'Webhook'} · {webhook.enabled ? 'enabled' : 'disabled'} · secret last4 {webhook.secret_last4 || '----'}</p><p className="muted">{webhook.target_url}</p><div className="buttonRow"><button type="button" onClick={() => { setActiveWebhookId(webhook.id); void toggleWebhook(webhook); }}>{webhook.enabled ? 'Disable' : 'Enable'}</button><button type="button" onClick={() => { setActiveWebhookId(webhook.id); void rotateWebhookSecret(webhook); }}>Rotate secret</button></div></div>)}<p className="muted">Recent webhook deliveries: {webhookDeliveries.length}</p>{webhookDeliveries.slice(0, 5).map((delivery) => <p key={delivery.id}>{delivery.event_type} · {delivery.status} · HTTP {delivery.response_status ?? '-'} {delivery.error_message ? `· ${delivery.error_message}` : ''}</p>)}</article></div></section>
  </main>;
}