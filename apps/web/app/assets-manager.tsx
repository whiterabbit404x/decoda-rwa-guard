'use client';

import { useEffect, useMemo, useState } from 'react';

import { usePilotAuth } from './pilot-auth-context';
import { parseTagInput } from './policy-builders';

type Props = { apiUrl: string };
type Asset = any;

const EMPTY_ASSET = {
  name: '', asset_type: 'wallet', chain_network: 'ethereum-mainnet', identifier: '', owner_team: '', tags: [] as string[], description: '', notes: '', enabled: true,
};

const ADDRESS_REGEX = /^0x[a-fA-F0-9]{40}$/;

export default function AssetsManager({ apiUrl }: Props) {
  const { authHeaders } = usePilotAuth();
  const [assets, setAssets] = useState<Asset[]>([]);
  const [form, setForm] = useState<any>(EMPTY_ASSET);
  const [search, setSearch] = useState('');
  const [filterType, setFilterType] = useState('all');
  const [message, setMessage] = useState('');
  const [showAdvanced, setShowAdvanced] = useState(false);

  async function load() {
    const response = await fetch(`${apiUrl}/assets`, { headers: { ...authHeaders() }, cache: 'no-store' });
    if (!response.ok) return;
    const payload = await response.json();
    setAssets(payload.assets ?? []);
  }

  async function createAsset() {
    if (!form.name.trim() || !form.identifier.trim()) {
      setMessage('Name and identifier are required.');
      return;
    }
    if (form.identifier.startsWith('0x') && !ADDRESS_REGEX.test(form.identifier)) {
      setMessage('Identifier looks like an address but is invalid. Use a full 0x-prefixed 40-byte address.');
      return;
    }
    const response = await fetch(`${apiUrl}/assets`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ ...form, tags: form.tags }),
    });
    const payload = await response.json().catch(() => ({}));
    setMessage(response.ok ? 'Asset created and added to registry.' : (payload.detail ?? 'Unable to create asset.'));
    if (!response.ok) return;
    setForm(EMPTY_ASSET);
    void load();
  }

  const filtered = useMemo(() => assets
    .filter((asset) => filterType === 'all' ? true : asset.asset_type === filterType)
    .filter((asset) => `${asset.name} ${asset.identifier} ${asset.chain_network} ${asset.owner_team || ''}`.toLowerCase().includes(search.toLowerCase())), [assets, search, filterType]);

  useEffect(() => { void load(); }, []);

  return (
    <div className="stack compactStack">
      <section className="dataCard">
        <div className="listHeader">
          <div>
            <h1>Asset registry</h1>
            <p className="muted">Assets are the wallets and contracts you protect. Add one to begin monitoring coverage.</p>
          </div>
          <button type="button" onClick={() => { document.getElementById('asset-create-form')?.scrollIntoView({ behavior: 'smooth' }); }}>Add asset</button>
        </div>
        <div className="buttonRow">
          <input placeholder="Search by name, chain, identifier, or owner" value={search} onChange={(event) => setSearch(event.target.value)} />
          <select value={filterType} onChange={(event) => setFilterType(event.target.value)}>
            <option value="all">All asset types</option>
            <option value="wallet">Wallet</option>
            <option value="contract">Contract</option>
            <option value="oracle">Oracle</option>
            <option value="treasury-linked asset">Treasury-linked asset</option>
          </select>
        </div>
        {filtered.length === 0 ? <div className="emptyStatePanel"><h4>No assets yet</h4><p className="muted">No assets match this view. Add the first wallet or contract you want to protect.</p><button type="button" onClick={() => { document.getElementById('asset-create-form')?.scrollIntoView({ behavior: 'smooth' }); }}>Add asset</button></div> : (
          <div className="tableWrap"><table><thead><tr><th>Name</th><th>Type</th><th>Chain</th><th>Identifier</th><th>Owner</th><th>Status</th></tr></thead><tbody>{filtered.map((asset) => <tr key={asset.id}><td>{asset.name}</td><td>{asset.asset_type}</td><td>{asset.chain_network}</td><td>{asset.identifier}</td><td>{asset.owner_team || 'Unassigned'}</td><td>{asset.enabled ? 'Verified / active' : 'Disabled'}</td></tr>)}</tbody></table></div>
        )}
      </section>

      <section id="asset-create-form" className="dataCard">
        <p className="sectionEyebrow">Create asset</p>
        <h2>Add your first protected system</h2>
        <input placeholder="Friendly name (e.g., Treasury wallet)" value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} />
        <div className="buttonRow">
          <select value={form.asset_type} onChange={(event) => setForm({ ...form, asset_type: event.target.value })}><option value="wallet">Wallet</option><option value="contract">Contract</option><option value="oracle">Oracle</option><option value="treasury-linked asset">Treasury-linked asset</option></select>
          <input placeholder="Chain / network (e.g., ethereum-mainnet)" value={form.chain_network} onChange={(event) => setForm({ ...form, chain_network: event.target.value })} />
        </div>
        <input placeholder="Wallet or contract address / identifier" value={form.identifier} onChange={(event) => setForm({ ...form, identifier: event.target.value.trim() })} />
        <input placeholder="Owner team (e.g., Treasury Ops)" value={form.owner_team} onChange={(event) => setForm({ ...form, owner_team: event.target.value })} />
        <button type="button" className="secondaryCta" onClick={() => setShowAdvanced((value) => !value)}>{showAdvanced ? 'Hide advanced settings' : 'Advanced settings'}</button>
        {showAdvanced ? <><input placeholder="Tags (comma-separated)" value={form.tags.join(', ')} onChange={(event) => setForm({ ...form, tags: parseTagInput(event.target.value) })} /><textarea rows={3} placeholder="Optional metadata / notes" value={form.notes} onChange={(event) => setForm({ ...form, notes: event.target.value })} /></> : null}
        <div className="buttonRow"><button type="button" onClick={() => void createAsset()}>Create asset</button></div>
        {message ? <p className="statusLine">{message}</p> : null}
      </section>
    </div>
  );
}
