'use client';

import { useEffect, useMemo, useState } from 'react';

import { usePilotAuth } from './pilot-auth-context';
import { parseTagInput } from './policy-builders';

type Props = { apiUrl: string };
type Asset = any;

const EMPTY_ASSET = { name: '', description: '', asset_type: 'contract', chain_network: 'ethereum-mainnet', identifier: '', asset_class: '', risk_tier: 'medium', owner_team: '', notes: '', enabled: true, tags: [] as string[] };

export default function AssetsManager({ apiUrl }: Props) {
  const { authHeaders } = usePilotAuth();
  const [assets, setAssets] = useState<Asset[]>([]);
  const [form, setForm] = useState<any>(EMPTY_ASSET);
  const [search, setSearch] = useState('');
  const [filterTier, setFilterTier] = useState('all');
  const [message, setMessage] = useState('');
  const [editing, setEditing] = useState<Asset | null>(null);

  async function load() {
    const response = await fetch(`${apiUrl}/assets`, { headers: { ...authHeaders() } });
    if (!response.ok) return;
    const payload = await response.json();
    setAssets(payload.assets ?? []);
  }

  async function createOrUpdate() {
    const body = { ...form, tags: form.tags };
    const target = editing ? `${apiUrl}/assets/${editing.id}` : `${apiUrl}/assets`;
    const method = editing ? 'PATCH' : 'POST';
    const response = await fetch(target, {
      method,
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify(body)
    });
    setMessage(response.ok ? (editing ? 'Asset updated.' : 'Asset created.') : 'Unable to save asset. Check required fields.');
    if (response.ok) {
      setForm(EMPTY_ASSET);
      setEditing(null);
      void load();
    }
  }

  async function toggleEnabled(asset: Asset) {
    await fetch(`${apiUrl}/assets/${asset.id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ ...asset, enabled: !asset.enabled })
    });
    void load();
  }

  async function archive(asset: Asset) {
    if (!confirm(`Archive asset ${asset.name}?`)) return;
    const response = await fetch(`${apiUrl}/assets/${asset.id}`, { method: 'DELETE', headers: { ...authHeaders() } });
    setMessage(response.ok ? 'Asset archived.' : 'Unable to archive asset.');
    if (response.ok) void load();
  }

  const filtered = useMemo(() => assets
    .filter((asset) => filterTier === 'all' ? true : asset.risk_tier === filterTier)
    .filter((asset) => `${asset.name} ${asset.identifier} ${asset.chain_network}`.toLowerCase().includes(search.toLowerCase()))
    .sort((a, b) => String(a.name).localeCompare(String(b.name))), [assets, search, filterTier]);

  useEffect(() => { void load(); }, []);

  return (
    <div className="dataCard">
      <h1>Assets</h1>
      <p className="muted">Track contracts, wallets, and monitored resources with rich metadata and ownership context.</p>
      <div className="buttonRow">
        <input placeholder="Search assets" value={search} onChange={(event) => setSearch(event.target.value)} />
        <select value={filterTier} onChange={(event) => setFilterTier(event.target.value)}>
          <option value="all">All risk tiers</option><option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option><option value="critical">Critical</option>
        </select>
      </div>

      <h3>{editing ? 'Edit asset' : 'Create asset'}</h3>
      <input placeholder="Asset name" value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} />
      <input placeholder="Description" value={form.description} onChange={(event) => setForm({ ...form, description: event.target.value })} />
      <select value={form.asset_type} onChange={(event) => setForm({ ...form, asset_type: event.target.value })}>
        <option value="contract">Contract</option><option value="wallet">Wallet</option><option value="treasury-linked asset">Treasury-linked asset</option><option value="oracle">Oracle</option><option value="custody component">Custody component</option><option value="settlement component">Settlement component</option><option value="admin-controlled module">Admin-controlled module</option><option value="monitored counterparty">Monitored counterparty</option><option value="policy-controlled workflow object">Policy-controlled workflow object</option>
      </select>
      <input placeholder="Chain/network" value={form.chain_network} onChange={(event) => setForm({ ...form, chain_network: event.target.value })} />
      <input placeholder="Contract, wallet, or identifier" value={form.identifier} onChange={(event) => setForm({ ...form, identifier: event.target.value })} />
      <input placeholder="Asset class" value={form.asset_class} onChange={(event) => setForm({ ...form, asset_class: event.target.value })} />
      <input placeholder="Owner team" value={form.owner_team} onChange={(event) => setForm({ ...form, owner_team: event.target.value })} />
      <input placeholder="Tags (comma-separated)" value={form.tags.join(', ')} onChange={(event) => setForm({ ...form, tags: parseTagInput(event.target.value) })} />
      <textarea placeholder="Notes" rows={3} value={form.notes} onChange={(event) => setForm({ ...form, notes: event.target.value })} />
      <div className="buttonRow">
        <button type="button" onClick={() => void createOrUpdate()}>{editing ? 'Save changes' : 'Create asset'}</button>
        {editing ? <button type="button" onClick={() => { setEditing(null); setForm(EMPTY_ASSET); }}>Cancel edit</button> : null}
      </div>
      {message ? <p className="statusLine">{message}</p> : null}

      <h3>Asset registry</h3>
      {filtered.length === 0 ? <p className="muted">No assets match this view. Create your first asset to start coverage.</p> : (
        <table><thead><tr><th>Name</th><th>Type</th><th>Network</th><th>Tier</th><th>Status</th><th>Owner</th><th>Actions</th></tr></thead>
          <tbody>{filtered.map((asset) => <tr key={asset.id}><td>{asset.name}<br /><span className="muted">{asset.identifier}</span></td><td>{asset.asset_type}</td><td>{asset.chain_network}</td><td>{asset.risk_tier}</td><td>{asset.enabled ? 'Enabled' : 'Disabled'}</td><td>{asset.owner_team || '—'}</td><td><div className="buttonRow"><button type="button" onClick={() => { setEditing(asset); setForm({ ...asset, tags: asset.tags ?? [] }); }}>Edit</button><button type="button" onClick={() => void toggleEnabled(asset)}>{asset.enabled ? 'Disable' : 'Enable'}</button><button type="button" onClick={() => void archive(asset)}>Archive</button></div></td></tr>)}</tbody></table>
      )}
    </div>
  );
}
