'use client';

import { useEffect, useState } from 'react';

import { usePilotAuth } from '../pilot-auth-context';

export default function IntegrationsPageClient({ apiUrl }: { apiUrl: string }) {
  const { authHeaders } = usePilotAuth();
  const [webhooks, setWebhooks] = useState<any[]>([]);
  const [deliveries, setDeliveries] = useState<any[]>([]);
  const [targetUrl, setTargetUrl] = useState('');
  const [description, setDescription] = useState('');
  const [message, setMessage] = useState('');

  async function load() {
    const response = await fetch(`${apiUrl}/integrations/webhooks`, { headers: authHeaders(), cache: 'no-store' });
    if (!response.ok) return;
    const payload = await response.json();
    const nextWebhooks = payload.webhooks ?? [];
    setWebhooks(nextWebhooks);
    if (nextWebhooks.length > 0) {
      const logResponse = await fetch(`${apiUrl}/integrations/webhooks/${nextWebhooks[0].id}/deliveries`, { headers: authHeaders(), cache: 'no-store' });
      if (logResponse.ok) setDeliveries((await logResponse.json()).deliveries ?? []);
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

  return <main className="productPage"><section className="featureSection"><div className="sectionHeader"><div><p className="eyebrow">Integrations</p><h1>Webhook management</h1><p className="lede">Create and manage signed webhooks, then inspect delivery history.</p></div></div><div className="threeColumnSection"><article className="dataCard"><p className="sectionEyebrow">Create webhook</p><input placeholder="https://example.com/webhooks/decoda" value={targetUrl} onChange={(event) => setTargetUrl(event.target.value)} /><input placeholder="Description" value={description} onChange={(event) => setDescription(event.target.value)} /><button type="button" onClick={() => void createWebhook()}>Create webhook</button>{message ? <p className="statusLine">{message}</p> : null}</article><article className="dataCard"><p className="sectionEyebrow">Configured webhooks</p>{webhooks.length === 0 ? <p className="muted">No webhooks configured.</p> : webhooks.map((item) => <div key={item.id} style={{ marginBottom: 10 }}><p>{item.target_url} · {item.enabled ? 'enabled' : 'disabled'} · last4 {item.secret_last4 || '----'}</p><div className="buttonRow"><button type="button" onClick={() => void toggleWebhook(item)}>{item.enabled ? 'Disable' : 'Enable'}</button><button type="button" onClick={() => void rotateSecret(item)}>Rotate secret</button></div></div>)}</article><article className="dataCard"><p className="sectionEyebrow">Recent delivery log</p>{deliveries.length === 0 ? <p className="muted">No deliveries yet.</p> : deliveries.map((delivery) => <p key={delivery.id}>{delivery.event_type} · {delivery.status} · HTTP {delivery.response_status ?? '-'} {delivery.error_message ? `· ${delivery.error_message}` : ''}</p>)}</article></div></section></main>;
}
