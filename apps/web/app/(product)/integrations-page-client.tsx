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
  const [webhookDeliveries, setWebhookDeliveries] = useState<any[]>([]);
  const [slackIntegrations, setSlackIntegrations] = useState<any[]>([]);
  const [slackDeliveries, setSlackDeliveries] = useState<any[]>([]);
  const [routingRules, setRoutingRules] = useState<RoutingRule[]>([]);
  const [targetUrl, setTargetUrl] = useState('');
  const [description, setDescription] = useState('');
  const [slackName, setSlackName] = useState('Incident room');
  const [slackWebhookUrl, setSlackWebhookUrl] = useState('');
  const [message, setMessage] = useState('');

  async function load() {
    const [webhookResponse, slackResponse, routingResponse] = await Promise.all([
      fetch(`${apiUrl}/integrations/webhooks`, { headers: authHeaders(), cache: 'no-store' }),
      fetch(`${apiUrl}/integrations/slack`, { headers: authHeaders(), cache: 'no-store' }),
      fetch(`${apiUrl}/integrations/routing`, { headers: authHeaders(), cache: 'no-store' }),
    ]);
    const nextWebhooks = webhookResponse.ok ? ((await webhookResponse.json()).webhooks ?? []) : [];
    const nextSlack = slackResponse.ok ? ((await slackResponse.json()).integrations ?? []) : [];
    setWebhooks(nextWebhooks);
    setSlackIntegrations(nextSlack);
    if (routingResponse.ok) setRoutingRules((await routingResponse.json()).rules ?? []);

    if (nextWebhooks.length > 0) {
      const logResponse = await fetch(`${apiUrl}/integrations/webhooks/${nextWebhooks[0].id}/deliveries`, { headers: authHeaders(), cache: 'no-store' });
      if (logResponse.ok) setWebhookDeliveries((await logResponse.json()).deliveries ?? []);
    }
    if (nextSlack.length > 0) {
      const logResponse = await fetch(`${apiUrl}/integrations/slack/${nextSlack[0].id}/deliveries`, { headers: authHeaders(), cache: 'no-store' });
      if (logResponse.ok) setSlackDeliveries((await logResponse.json()).deliveries ?? []);
    }
  }

  useEffect(() => { void load(); }, []);

  async function createWebhook() {
    const response = await fetch(`${apiUrl}/integrations/webhooks`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ target_url: targetUrl, description, event_types: ['alert.created'] }),
    });
    const payload = response.ok ? await response.json() : null;
    setMessage(response.ok ? `Webhook created. Save secret now: ${payload.secret}` : 'Failed to create webhook.');
    if (response.ok) {
      setTargetUrl('');
      setDescription('');
      await load();
    }
  }

  async function createSlackIntegration() {
    const response = await fetch(`${apiUrl}/integrations/slack`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ display_name: slackName, webhook_url: slackWebhookUrl }),
    });
    setMessage(response.ok ? 'Slack integration created.' : 'Unable to create Slack integration.');
    if (response.ok) {
      setSlackWebhookUrl('');
      await load();
    }
  }

  async function toggleWebhook(item: any) {
    const response = await fetch(`${apiUrl}/integrations/webhooks/${item.id}`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ enabled: !item.enabled, description: item.description || '' }),
    });
    setMessage(response.ok ? 'Webhook updated.' : 'Unable to update webhook.');
    if (response.ok) await load();
  }

  async function rotateSecret(item: any) {
    const response = await fetch(`${apiUrl}/integrations/webhooks/${item.id}/rotate-secret`, {
      method: 'POST', headers: authHeaders(),
    });
    const payload = response.ok ? await response.json() : null;
    setMessage(response.ok ? `Secret rotated. Save now: ${payload.secret}` : 'Unable to rotate secret.');
  }

  async function toggleSlack(item: any) {
    const response = await fetch(`${apiUrl}/integrations/slack/${item.id}`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ enabled: !item.enabled }),
    });
    setMessage(response.ok ? 'Slack integration updated.' : 'Unable to update Slack integration.');
    if (response.ok) await load();
  }

  async function testSlack(item: any) {
    const response = await fetch(`${apiUrl}/integrations/slack/${item.id}/test`, {
      method: 'POST', headers: authHeaders(),
    });
    setMessage(response.ok ? 'Slack test notification queued.' : 'Slack test failed.');
    if (response.ok) await load();
  }

  async function deleteSlack(item: any) {
    const response = await fetch(`${apiUrl}/integrations/slack/${item.id}`, { method: 'DELETE', headers: authHeaders() });
    setMessage(response.ok ? 'Slack integration deleted.' : 'Unable to delete Slack integration.');
    if (response.ok) await load();
  }

  async function updateRouting(channelType: RoutingRule['channel_type'], severityThreshold: RoutingRule['severity_threshold'], enabled: boolean) {
    const response = await fetch(`${apiUrl}/integrations/routing/${channelType}`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ severity_threshold: severityThreshold, enabled, event_types: ['alert.created'] }),
    });
    setMessage(response.ok ? `${channelType} routing updated.` : `Unable to update ${channelType} routing.`);
    if (response.ok) await load();
  }

  const deliveryFailures = useMemo(
    () => [...webhookDeliveries, ...slackDeliveries].filter((item) => item.status !== 'succeeded').length,
    [webhookDeliveries, slackDeliveries],
  );

  function ruleFor(channel: RoutingRule['channel_type']): RoutingRule {
    return routingRules.find((item) => item.channel_type === channel) ?? { channel_type: channel, severity_threshold: 'medium', enabled: true };
  }

  return <main className="productPage"><section className="featureSection"><div className="sectionHeader"><div><p className="eyebrow">Integrations</p><h1>Alert channels and routing</h1><p className="lede">Connect webhook + Slack destinations, test delivery, and define per-channel routing thresholds.</p></div></div><div className="threeColumnSection"><article className="dataCard"><p className="sectionEyebrow">Delivery health</p><p className="muted">Active webhooks: {webhooks.filter((item) => item.enabled).length}</p><p className="muted">Active Slack channels: {slackIntegrations.filter((item) => item.enabled).length}</p><p className="muted">Recent failures: {deliveryFailures}</p>{message ? <p className="statusLine">{message}</p> : null}</article><article className="dataCard"><p className="sectionEyebrow">Create webhook</p><input placeholder="https://example.com/webhooks/decoda" value={targetUrl} onChange={(event) => setTargetUrl(event.target.value)} /><input placeholder="Description" value={description} onChange={(event) => setDescription(event.target.value)} /><button type="button" onClick={() => void createWebhook()}>Create webhook</button></article><article className="dataCard"><p className="sectionEyebrow">Create Slack integration</p><input placeholder="Display name" value={slackName} onChange={(event) => setSlackName(event.target.value)} /><input placeholder="https://hooks.slack.com/services/..." value={slackWebhookUrl} onChange={(event) => setSlackWebhookUrl(event.target.value)} /><button type="button" onClick={() => void createSlackIntegration()}>Add Slack channel</button><p className="muted">Slack v1 uses incoming webhooks with top-level text fallback.</p></article></div></section><section className="featureSection"><div className="sectionHeader"><div><p className="eyebrow">Alert routing</p><h2>Per-channel thresholds</h2></div></div><div className="threeColumnSection">{CHANNELS.map((channel) => { const rule = ruleFor(channel); return <article key={channel} className="dataCard"><p className="sectionEyebrow">{channel}</p><div className="buttonRow"><select value={rule.severity_threshold} onChange={(event) => void updateRouting(channel, event.target.value as RoutingRule['severity_threshold'], rule.enabled)}><option value="low">low</option><option value="medium">medium</option><option value="high">high</option><option value="critical">critical</option></select><button type="button" onClick={() => void updateRouting(channel, rule.severity_threshold, !rule.enabled)}>{rule.enabled ? 'Disable' : 'Enable'}</button></div></article>; })}</div></section><section className="featureSection"><div className="sectionHeader"><div><p className="eyebrow">Configured channels</p><h2>Webhook and Slack management</h2></div></div><div className="threeColumnSection"><article className="dataCard"><p className="sectionEyebrow">Webhooks</p>{webhooks.length === 0 ? <p className="muted">No webhooks configured.</p> : webhooks.map((item) => <div key={item.id} style={{ marginBottom: 10 }}><p>{item.target_url} · {item.enabled ? 'enabled' : 'disabled'} · last4 {item.secret_last4 || '----'}</p><div className="buttonRow"><button type="button" onClick={() => void toggleWebhook(item)}>{item.enabled ? 'Disable' : 'Enable'}</button><button type="button" onClick={() => void rotateSecret(item)}>Rotate secret</button></div></div>)}</article><article className="dataCard"><p className="sectionEyebrow">Slack</p>{slackIntegrations.length === 0 ? <p className="muted">No Slack integrations configured.</p> : slackIntegrations.map((item) => <div key={item.id} style={{ marginBottom: 10 }}><p>{item.display_name} · {item.enabled ? 'enabled' : 'disabled'} · last4 {item.webhook_last4 || '----'}</p><div className="buttonRow"><button type="button" onClick={() => void toggleSlack(item)}>{item.enabled ? 'Disable' : 'Enable'}</button><button type="button" onClick={() => void testSlack(item)}>Test send</button><button type="button" onClick={() => void deleteSlack(item)}>Delete</button></div></div>)}</article><article className="dataCard"><p className="sectionEyebrow">Recent delivery failures</p>{[...webhookDeliveries, ...slackDeliveries].length === 0 ? <p className="muted">No deliveries yet.</p> : [...webhookDeliveries, ...slackDeliveries].slice(0, 10).map((delivery) => <p key={delivery.id}>{delivery.event_type} · {delivery.status} · HTTP {delivery.response_status ?? '-'} {delivery.error_message ? `· ${delivery.error_message}` : ''}</p>)}</article></div></section></main>;
}
