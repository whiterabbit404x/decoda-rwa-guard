'use client';

import { useEffect, useMemo, useState } from 'react';

import { usePilotAuth } from './pilot-auth-context';
import { parseTagInput } from './policy-builders';

type Props = { apiUrl: string };
type Target = any;

const EMPTY_TARGET = {
  name: '',
  target_type: 'contract',
  chain_network: 'ethereum',
  contract_identifier: '',
  wallet_address: '',
  asset_type: '',
  owner_notes: '',
  severity_preference: 'medium',
  enabled: true,
  monitoring_enabled: false,
  monitoring_interval_seconds: 300,
  severity_threshold: 'medium',
  auto_create_alerts: true,
  auto_create_incidents: false,
  monitoring_scenario: '',
  tags: [] as string[],
};

export default function TargetsManager({ apiUrl }: Props) {
  const { authHeaders } = usePilotAuth();
  const [targets, setTargets] = useState<Target[]>([]);
  const [assets, setAssets] = useState<any[]>([]);
  const [form, setForm] = useState<any>(EMPTY_TARGET);
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');
  const [message, setMessage] = useState('');
  const [editing, setEditing] = useState<Target | null>(null);

  async function load() {
    const [targetsResponse, assetsResponse] = await Promise.all([
      fetch(`${apiUrl}/targets`, { headers: { ...authHeaders() } }),
      fetch(`${apiUrl}/assets`, { headers: { ...authHeaders() } }),
    ]);
    if (targetsResponse.ok) {
      const payload = await targetsResponse.json();
      setTargets(payload.targets ?? []);
    }
    if (assetsResponse.ok) {
      const payload = await assetsResponse.json();
      setAssets(payload.assets ?? []);
    }
  }

  async function createOrUpdate() {
    const target = editing ? `${apiUrl}/targets/${editing.id}` : `${apiUrl}/targets`;
    const method = editing ? 'PATCH' : 'POST';
    const response = await fetch(target, {
      method,
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify(form)
    });
    setMessage(response.ok ? (editing ? 'Target updated.' : 'Target created.') : 'Unable to save target.');
    if (response.ok) {
      setForm(EMPTY_TARGET);
      setEditing(null);
      void load();
    }
  }

  async function toggleEnabled(target: Target) {
    await fetch(`${apiUrl}/targets/${target.id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ ...target, enabled: !target.enabled })
    });
    void load();
  }

  async function toggleMonitoring(target: Target) {
    await fetch(`${apiUrl}/monitoring/targets/${target.id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({
        monitoring_enabled: !target.monitoring_enabled,
        monitoring_mode: target.monitoring_mode || 'poll',
        monitoring_interval_seconds: target.monitoring_interval_seconds || 300,
        severity_threshold: target.severity_threshold || 'medium',
        auto_create_alerts: target.auto_create_alerts ?? true,
        auto_create_incidents: target.auto_create_incidents ?? false,
        monitoring_scenario: target.monitoring_scenario || null,
        notification_channels: target.notification_channels || [],
        is_active: target.is_active ?? true,
      }),
    });
    void load();
  }

  async function remove(target: Target) {
    if (!confirm(`Delete target ${target.name}? This cannot be undone.`)) return;
    const response = await fetch(`${apiUrl}/targets/${target.id}`, { method: 'DELETE', headers: authHeaders() });
    setMessage(response.ok ? 'Target deleted.' : 'Unable to delete target.');
    if (response.ok) void load();
  }

  function duplicate(target: Target) {
    setEditing(null);
    setForm({ ...target, name: `${target.name} (copy)` });
    setMessage('Target duplicated into form. Update details and save.');
  }

  const filtered = useMemo(() => targets
    .filter((item) => statusFilter === 'all' ? true : statusFilter === 'enabled' ? item.enabled : !item.enabled)
    .filter((item) => `${item.name} ${item.chain_network} ${item.contract_identifier || ''} ${item.wallet_address || ''}`.toLowerCase().includes(search.toLowerCase())), [targets, search, statusFilter]);

  useEffect(() => { void load(); }, []);

  return (
    <div className="dataCard">
      <h1>Targets</h1>
      <p className="muted">Create and manage monitored targets with ownership, severity, and notes for team operations.</p>
      <div className="buttonRow">
        <input placeholder="Search targets" value={search} onChange={(event) => setSearch(event.target.value)} />
        <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}><option value="all">All statuses</option><option value="enabled">Enabled</option><option value="disabled">Disabled</option></select>
      </div>

      <h3>{editing ? 'Edit target' : 'Create target'}</h3>
      <input placeholder="Target name" value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} />
      <select value={form.target_type} onChange={(event) => setForm({ ...form, target_type: event.target.value })}>
        <option value="contract">Contract</option><option value="wallet">Wallet</option><option value="oracle">Oracle</option><option value="treasury-linked asset">Treasury-linked asset</option><option value="settlement component">Settlement component</option><option value="admin-controlled module">Admin-controlled module</option>
      </select>
      <input placeholder="Chain/network" value={form.chain_network} onChange={(event) => setForm({ ...form, chain_network: event.target.value })} />
      <input placeholder="Contract identifier" value={form.contract_identifier} onChange={(event) => setForm({ ...form, contract_identifier: event.target.value })} />
      <input placeholder="Wallet (0x...)" value={form.wallet_address} onChange={(event) => setForm({ ...form, wallet_address: event.target.value })} />
      <input placeholder="Asset class/type" value={form.asset_type} onChange={(event) => setForm({ ...form, asset_type: event.target.value })} />
      <select value={form.asset_id || ''} onChange={(event) => setForm({ ...form, asset_id: event.target.value || null })}>
        <option value="">No asset profile linked</option>
        {assets.map((asset) => <option key={asset.id} value={asset.id}>{asset.name} ({asset.asset_class || 'n/a'})</option>)}
      </select>
      <select value={form.severity_preference} onChange={(event) => setForm({ ...form, severity_preference: event.target.value })}>
        <option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option><option value="critical">Critical</option>
      </select>
      <label className="muted">Automatic monitoring</label>
      <div className="buttonRow">
        <label><input type="checkbox" checked={form.monitoring_enabled} onChange={(event) => setForm({ ...form, monitoring_enabled: event.target.checked })} /> Enabled</label>
        <label><input type="checkbox" checked={form.auto_create_alerts} onChange={(event) => setForm({ ...form, auto_create_alerts: event.target.checked })} /> Auto alerts</label>
        <label><input type="checkbox" checked={form.auto_create_incidents} onChange={(event) => setForm({ ...form, auto_create_incidents: event.target.checked })} /> Auto incidents</label>
      </div>
      <div className="buttonRow">
        <input type="number" min={30} step={30} value={form.monitoring_interval_seconds} onChange={(event) => setForm({ ...form, monitoring_interval_seconds: Number(event.target.value) || 300 })} />
        <select value={form.severity_threshold} onChange={(event) => setForm({ ...form, severity_threshold: event.target.value })}>
          <option value="low">Threshold: low</option><option value="medium">Threshold: medium</option><option value="high">Threshold: high</option><option value="critical">Threshold: critical</option>
        </select>
      </div>
      <input placeholder="Tags (comma-separated)" value={form.tags.join(', ')} onChange={(event) => setForm({ ...form, tags: parseTagInput(event.target.value) })} />
      <textarea placeholder="Notes" rows={3} value={form.owner_notes} onChange={(event) => setForm({ ...form, owner_notes: event.target.value })} />
      <div className="buttonRow">
        <button type="button" onClick={() => void createOrUpdate()}>{editing ? 'Save target' : 'Create target'}</button>
        {editing ? <button type="button" onClick={() => { setEditing(null); setForm(EMPTY_TARGET); }}>Cancel edit</button> : null}
      </div>
      {message ? <p className="statusLine">{message}</p> : null}

      <h3>Target registry</h3>
      {filtered.length === 0 ? <p className="muted">Create your first target to run live analysis and start alerting.</p> : filtered.map((target) => (
        <div key={target.id} className="listHeader" style={{ marginBottom: 8 }}>
          <span>{target.name} · {target.target_type} · {target.chain_network} · asset profile: {target.asset_id || 'none'} · {target.enabled ? 'enabled' : 'disabled'} · monitoring: {target.monitoring_enabled ? 'active' : 'paused'} · interval: {target.monitoring_interval_seconds ?? 300}s · last check: {target.last_checked_at ? new Date(target.last_checked_at).toLocaleString() : 'never'}</span>
          <div className="buttonRow"><button type="button" onClick={() => { setEditing(target); setForm({ ...target, tags: target.tags ?? [] }); }}>Edit</button><button type="button" onClick={() => duplicate(target)}>Duplicate</button><button type="button" onClick={() => void toggleEnabled(target)}>{target.enabled ? 'Disable' : 'Enable'}</button><button type="button" onClick={() => void toggleMonitoring(target)}>{target.monitoring_enabled ? 'Pause monitoring' : 'Enable monitoring'}</button><button type="button" onClick={() => void remove(target)}>Delete</button></div>
        </div>
      ))}
    </div>
  );
}
