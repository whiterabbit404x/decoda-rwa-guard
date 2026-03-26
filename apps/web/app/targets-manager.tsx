'use client';

import { useEffect, useMemo, useState } from 'react';

import { usePilotAuth } from './pilot-auth-context';
import { parseTagInput } from './policy-builders';

type Props = { apiUrl: string };
type Target = any;

const EMPTY_TARGET = { name: '', target_type: 'contract', chain_network: 'ethereum', contract_identifier: '', wallet_address: '', asset_type: '', owner_notes: '', severity_preference: 'medium', enabled: true, tags: [] as string[] };

export default function TargetsManager({ apiUrl }: Props) {
  const { authHeaders } = usePilotAuth();
  const [targets, setTargets] = useState<Target[]>([]);
  const [form, setForm] = useState<any>(EMPTY_TARGET);
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');
  const [message, setMessage] = useState('');
  const [editing, setEditing] = useState<Target | null>(null);

  async function load() {
    const response = await fetch(`${apiUrl}/targets`, { headers: { ...authHeaders() } });
    if (!response.ok) return;
    const payload = await response.json();
    setTargets(payload.targets ?? []);
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
      <select value={form.severity_preference} onChange={(event) => setForm({ ...form, severity_preference: event.target.value })}>
        <option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option><option value="critical">Critical</option>
      </select>
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
          <span>{target.name} · {target.target_type} · {target.chain_network} · {target.enabled ? 'enabled' : 'disabled'}</span>
          <div className="buttonRow"><button type="button" onClick={() => { setEditing(target); setForm({ ...target, tags: target.tags ?? [] }); }}>Edit</button><button type="button" onClick={() => duplicate(target)}>Duplicate</button><button type="button" onClick={() => void toggleEnabled(target)}>{target.enabled ? 'Disable' : 'Enable'}</button><button type="button" onClick={() => void remove(target)}>Delete</button></div>
        </div>
      ))}
    </div>
  );
}
