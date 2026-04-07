'use client';

import { useEffect, useMemo, useState } from 'react';

import { usePilotAuth } from './pilot-auth-context';

type Props = { apiUrl: string };
type Target = any;

const EMPTY_TARGET = {
  name: '',
  asset_id: '',
  target_type: 'wallet',
  chain_network: 'ethereum-mainnet',
  contract_identifier: '',
  wallet_address: '',
  owner_notes: '',
  monitoring_enabled: true,
  severity_threshold: 'medium',
  auto_create_incidents: false,
  auto_create_alerts: true,
  monitoring_interval_seconds: 300,
};

export default function TargetsManager({ apiUrl }: Props) {
  const { authHeaders } = usePilotAuth();
  const [targets, setTargets] = useState<Target[]>([]);
  const [assets, setAssets] = useState<any[]>([]);
  const [form, setForm] = useState<any>(EMPTY_TARGET);
  const [search, setSearch] = useState('');
  const [message, setMessage] = useState('');

  async function load() {
    const [targetsResponse, assetsResponse] = await Promise.all([
      fetch(`${apiUrl}/targets`, { headers: { ...authHeaders() }, cache: 'no-store' }),
      fetch(`${apiUrl}/assets`, { headers: { ...authHeaders() }, cache: 'no-store' }),
    ]);
    if (targetsResponse.ok) setTargets((await targetsResponse.json()).targets ?? []);
    if (assetsResponse.ok) setAssets((await assetsResponse.json()).assets ?? []);
  }

  async function createTarget() {
    if (!form.asset_id) {
      setMessage('Select an asset first. Targets define behavior monitored for a specific asset.');
      return;
    }
    const response = await fetch(`${apiUrl}/targets`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify(form),
    });
    const payload = await response.json().catch(() => ({}));
    setMessage(response.ok ? 'Target created and monitoring configuration saved.' : (payload.detail ?? 'Unable to create target.'));
    if (!response.ok) return;
    setForm(EMPTY_TARGET);
    void load();
  }

  async function toggleTarget(target: Target) {
    const enable = !(target.enabled && target.monitoring_enabled);
    const response = await fetch(`${apiUrl}/targets/${target.id}/${enable ? 'enable' : 'disable'}`, { method: 'POST', headers: authHeaders() });
    if (!response.ok) {
      setMessage('Unable to update target state.');
      return;
    }
    setMessage(enable ? 'Target enabled.' : 'Target disabled.');
    void load();
  }

  const filtered = useMemo(() => targets
    .filter((item) => `${item.name} ${item.chain_network} ${item.target_type}`.toLowerCase().includes(search.toLowerCase())), [targets, search]);

  useEffect(() => { void load(); }, []);

  return (
    <div className="stack compactStack">
      <section className="dataCard">
        <h1>Monitoring targets</h1>
        <p className="muted">Assets are what you protect. Targets define what behavior you monitor.</p>
        <input placeholder="Search targets" value={search} onChange={(event) => setSearch(event.target.value)} />
        {filtered.length === 0 ? <div className="emptyStatePanel"><h4>No monitoring targets yet</h4><p className="muted">Create a target to define what activity should trigger alerts.</p><a href="#target-create-form">Create target</a></div> : (
          <div className="stack compactStack">{filtered.map((target) => (
            <article key={target.id} className="overviewListItem">
              <div>
                <p><strong>{target.name}</strong> · {target.target_type}</p>
                <p className="muted">Asset: {assets.find((asset) => asset.id === target.asset_id)?.name || 'Unlinked'} · Rules: {target.owner_notes || 'Default monitoring rules'}.</p>
                <p className="tableMeta">Health: {target.last_checked_at ? 'Active' : (target.monitoring_enabled ? 'Idle' : 'Disabled')} · Last evaluation: {target.last_checked_at ? new Date(target.last_checked_at).toLocaleString() : 'Never'}</p>
              </div>
              <div className="buttonRow"><button type="button" onClick={() => void toggleTarget(target)}>{target.enabled && target.monitoring_enabled ? 'Disable' : 'Enable'}</button></div>
            </article>
          ))}</div>
        )}
      </section>

      <section id="target-create-form" className="dataCard">
        <p className="sectionEyebrow">Create target</p>
        <h2>Define behavior to monitor</h2>
        <select value={form.asset_id} onChange={(event) => setForm({ ...form, asset_id: event.target.value })}>
          <option value="">Select asset</option>
          {assets.map((asset) => <option key={asset.id} value={asset.id}>{asset.name} ({asset.chain_network})</option>)}
        </select>
        <input placeholder="Target name" value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} />
        <div className="buttonRow">
          <select value={form.target_type} onChange={(event) => setForm({ ...form, target_type: event.target.value })}>
            <option value="wallet">Transactions</option>
            <option value="contract">Contract interactions</option>
            <option value="admin-controlled module">Admin role changes</option>
            <option value="oracle">Oracle freshness</option>
          </select>
          <select value={form.severity_threshold} onChange={(event) => setForm({ ...form, severity_threshold: event.target.value })}><option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option><option value="critical">Critical</option></select>
          <input type="number" min={30} step={30} value={form.monitoring_interval_seconds} onChange={(event) => setForm({ ...form, monitoring_interval_seconds: Number(event.target.value) || 300 })} />
        </div>
        <textarea rows={3} placeholder="Rules summary (thresholds, counterparties, special conditions)" value={form.owner_notes} onChange={(event) => setForm({ ...form, owner_notes: event.target.value })} />
        <div className="buttonRow">
          <label><input type="checkbox" checked={form.auto_create_alerts} onChange={(event) => setForm({ ...form, auto_create_alerts: event.target.checked })} /> Auto-alert channels</label>
          <label><input type="checkbox" checked={form.auto_create_incidents} onChange={(event) => setForm({ ...form, auto_create_incidents: event.target.checked })} /> Auto-open incidents</label>
        </div>
        <button type="button" onClick={() => void createTarget()}>Create target</button>
        {message ? <p className="statusLine">{message}</p> : null}
      </section>
    </div>
  );
}
