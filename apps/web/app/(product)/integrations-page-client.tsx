'use client';

import { useEffect, useMemo, useState } from 'react';

import { usePilotAuth } from '../pilot-auth-context';

type RoutingRule = {
  channel_type: 'dashboard' | 'email' | 'webhook' | 'slack';
  severity_threshold: 'low' | 'medium' | 'high' | 'critical';
  enabled: boolean;
};

const CHANNELS: Array<RoutingRule['channel_type']> = ['dashboard', 'email', 'webhook', 'slack'];

export default function IntegrationsPageClient({ apiUrl }: { apiUrl: string }) {
  const { authHeaders } = usePilotAuth();
  const [webhooks, setWebhooks] = useState<any[]>([]);
  const [slackIntegrations, setSlackIntegrations] = useState<any[]>([]);
  const [slackDeliveries, setSlackDeliveries] = useState<any[]>([]);
  const [routingRules, setRoutingRules] = useState<RoutingRule[]>([]);
  const [health, setHealth] = useState<any>(null);

  const [targetUrl, setTargetUrl] = useState('');
  const [description, setDescription] = useState('');
  const [slackName, setSlackName] = useState('Incident room');
  const [slackMode, setSlackMode] = useState<'webhook' | 'bot'>('webhook');
  const [slackWebhookUrl, setSlackWebhookUrl] = useState('');
  const [slackBotToken, setSlackBotToken] = useState('');
  const [slackChannel, setSlackChannel] = useState('');
  const [message, setMessage] = useState('');

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

    if (nextSlack.length > 0) {
      const logResponse = await fetch(`${apiUrl}/integrations/slack/${nextSlack[0].id}/deliveries`, { headers: authHeaders(), cache: 'no-store' });
      setSlackDeliveries(logResponse.ok ? ((await logResponse.json()).deliveries ?? []) : []);
    } else {
      setSlackDeliveries([]);
    }
  }

  useEffect(() => { void loadAll(); }, []);

  async function createSlackIntegration() {
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
    setMessage(response.ok ? `Slack (${slackMode}) integration saved.` : 'Unable to create Slack integration.');
    if (response.ok) {
      setSlackWebhookUrl('');
      setSlackBotToken('');
      setSlackChannel('');
      void loadAll();
    }
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
    if (!slackIntegrations[0]) {
      setMessage('No Slack channel configured yet. Add one before running a test.');
      return;
    }
    const response = await fetch(`${apiUrl}/system/integrations/test-slack`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ integration_id: slackIntegrations[0].id })
    });
    setMessage(response.ok ? 'Slack health test queued.' : 'Slack health test failed.');
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
    <section className="featureSection"><div className="sectionHeader"><div><p className="eyebrow">System readiness</p><h1>Integrations health and delivery</h1><p className="lede">See Stripe, email, and Slack readiness before customers depend on production alerts.</p></div></div>
      <div className="threeColumnSection">
        <article className="dataCard"><p className="sectionEyebrow">Health summary</p>
          <p className="muted">Stripe: {health?.stripe?.status || 'unknown'} · {health?.stripe?.message || 'No diagnostics yet.'}</p>
          <p className="muted">Email: {health?.email?.status || 'unknown'} · {health?.email?.message || 'No diagnostics yet.'}</p>
          <p className="muted">Slack: {health?.slack?.status || 'unknown'} · Active channels: {slackIntegrations.filter((item) => item.enabled).length}</p>
          <p className="muted">Recent Slack failures: {deliveryFailures}</p>
          {message ? <p className="statusLine">{message}</p> : null}
        </article>
        <article className="dataCard"><p className="sectionEyebrow">Safe test actions</p><button type="button" onClick={() => void runEmailTest()}>Test email delivery</button><button type="button" onClick={() => void runSlackHealthTest()}>Test Slack delivery</button><p className="muted">Billing is not fully configured if Stripe checks report warnings.</p></article>
        <article className="dataCard"><p className="sectionEyebrow">Create Slack integration</p>
          <input placeholder="Display name" value={slackName} onChange={(event) => setSlackName(event.target.value)} />
          <select value={slackMode} onChange={(event) => setSlackMode(event.target.value as 'webhook' | 'bot')}><option value="webhook">Incoming webhook (compatibility)</option><option value="bot">Bot token (recommended)</option></select>
          {slackMode === 'webhook' ? <input placeholder="https://hooks.slack.com/services/..." value={slackWebhookUrl} onChange={(event) => setSlackWebhookUrl(event.target.value)} /> : <input placeholder="xoxb-..." value={slackBotToken} onChange={(event) => setSlackBotToken(event.target.value)} />}
          <input placeholder="#alerts or C012345" value={slackChannel} onChange={(event) => setSlackChannel(event.target.value)} />
          <button type="button" onClick={() => void createSlackIntegration()}>Save Slack integration</button>
        </article>
      </div>
    </section>

    <section className="featureSection"><div className="sectionHeader"><div><p className="eyebrow">Routing</p><h2>Per-channel thresholds</h2></div></div><div className="threeColumnSection">{CHANNELS.map((channel) => { const rule = ruleFor(channel); return <article key={channel} className="dataCard"><p className="sectionEyebrow">{channel}</p><div className="buttonRow"><select value={rule.severity_threshold} onChange={(event) => void updateRouting(channel, event.target.value as RoutingRule['severity_threshold'], rule.enabled)}><option value="low">low</option><option value="medium">medium</option><option value="high">high</option><option value="critical">critical</option></select><button type="button" onClick={() => void updateRouting(channel, rule.severity_threshold, !rule.enabled)}>{rule.enabled ? 'Disable' : 'Enable'}</button></div></article>; })}</div></section>

    <section className="featureSection"><div className="sectionHeader"><div><p className="eyebrow">Configured Slack channels</p><h2>Mode-aware management</h2></div></div><div className="threeColumnSection"><article className="dataCard"><p className="sectionEyebrow">Slack</p>{slackIntegrations.length === 0 ? <p className="muted">No Slack channel configured yet.</p> : slackIntegrations.map((item) => <div key={item.id} style={{ marginBottom: 10 }}><p>{item.display_name} · {item.enabled ? 'enabled' : 'disabled'} · mode {item.slack_mode || 'webhook'} · secret last4 {(item.bot_token_last4 || item.webhook_last4) || '----'}</p><div className="buttonRow"><button type="button" onClick={() => void testSlack(item)}>Test send</button></div></div>)}</article><article className="dataCard"><p className="sectionEyebrow">Recent Slack failures</p>{slackDeliveries.length === 0 ? <p className="muted">No Slack deliveries yet.</p> : slackDeliveries.slice(0, 10).map((delivery) => <p key={delivery.id}>{delivery.event_type} · {delivery.provider_mode || 'webhook'} · {delivery.status} · HTTP {delivery.response_status ?? '-'} {delivery.error_message ? `· ${delivery.error_message}` : ''}</p>)}</article><article className="dataCard"><p className="sectionEyebrow">Webhooks</p><input placeholder="https://example.com/webhooks/decoda" value={targetUrl} onChange={(event) => setTargetUrl(event.target.value)} /><input placeholder="Description" value={description} onChange={(event) => setDescription(event.target.value)} /><p className="muted">Existing webhooks: {webhooks.length}. Create/edit remains available in API and legacy controls.</p></article></div></section>
  </main>;
}
