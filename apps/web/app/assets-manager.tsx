'use client';

import { useEffect, useMemo, useState } from 'react';

import { usePilotAuth } from './pilot-auth-context';
import { parseTagInput } from './policy-builders';

type Props = { apiUrl: string };
type Asset = any;

const EMPTY_ASSET = {
  name: '', asset_type: 'wallet', chain_network: 'ethereum-mainnet', identifier: '', owner_team: '', tags: [] as string[], description: '', notes: '', enabled: true,
};

const QUICK_PRESETS = [
  { label: 'Ethereum Wallet', form: { name: 'Treasury wallet', asset_type: 'wallet', chain_network: 'ethereum-mainnet' } },
  { label: 'Smart Contract', form: { name: 'Core protocol contract', asset_type: 'contract', chain_network: 'ethereum-mainnet' } },
  { label: 'Treasury Vault', form: { name: 'Treasury vault', asset_type: 'treasury-linked asset', chain_network: 'ethereum-mainnet' } },
] as const;

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
    setMessage(response.ok ? `Asset created. Verification status: ${payload.verification_status ?? 'pending'}.` : (payload.detail ?? 'Unable to create asset.'));
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
            <p className="muted">Assets are the wallets, contracts, and treasury systems you protect.</p>
          </div>
          <button type="button" onClick={() => { document.getElementById('asset-create-form')?.scrollIntoView({ behavior: 'smooth' }); }}>Add asset</button>
        </div>
        <div className="chipRow">
          {QUICK_PRESETS.map((preset) => <button key={preset.label} type="button" className="secondaryCta" onClick={() => setForm({ ...form, ...preset.form, identifier: form.identifier || '0x5f6f35FD8b10C5576089f99C7c8c351Deb851d1F' })}>{preset.label}</button>)}
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
          <div className="tableWrap"><table><thead><tr><th>Name</th><th>Type</th><th>Chain</th><th>Identifier</th><th>Status</th></tr></thead><tbody>{filtered.map((asset) => <tr key={asset.id}><td>{asset.name}</td><td>{asset.asset_type}</td><td>{asset.chain_network}</td><td><p>{asset.identifier}</p><p className="tableMeta">Normalized: {asset.normalized_identifier || asset.identifier}</p></td><td><div className="chipRow"><span className="ruleChip">{asset.verification_status === 'verified' ? 'Verified' : asset.verification_status === 'pending' ? 'Pending verification' : 'Needs attention'}</span><span className="ruleChip">{asset.verification_summary?.recent_activity || 'recent activity unknown'}</span><span className="ruleChip">{asset.monitoring_target_count > 0 ? 'Monitoring attached' : 'No targets yet'}</span></div></td></tr>)}</tbody></table></div>
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
        <input placeholder="Wallet or contract address / identifier (e.g., 0x5f6f35FD8b10C5576089f99C7c8c351Deb851d1F)" value={form.identifier} onChange={(event) => setForm({ ...form, identifier: event.target.value.trim() })} />
        <input placeholder="Owner team (e.g., Treasury Ops)" value={form.owner_team} onChange={(event) => setForm({ ...form, owner_team: event.target.value })} />
        <button type="button" className="secondaryCta" onClick={() => setShowAdvanced((value) => !value)}>{showAdvanced ? 'Hide advanced settings' : 'Advanced settings'}</button>
        {showAdvanced ? <><input placeholder="Tags (comma-separated)" value={form.tags.join(', ')} onChange={(event) => setForm({ ...form, tags: parseTagInput(event.target.value) })} /><textarea rows={3} placeholder="Optional metadata / notes" value={form.notes} onChange={(event) => setForm({ ...form, notes: event.target.value })} /></> : null}
        <div className="buttonRow"><button type="button" onClick={() => void createAsset()}>Create asset</button></div>
        {message ? <p className="statusLine">{message}</p> : null}
      </section>
    </div>
  );
}
