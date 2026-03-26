'use client';

import { useEffect, useState } from 'react';

import { usePilotAuth } from './pilot-auth-context';

type Props = { apiUrl: string };

export default function AssetsManager({ apiUrl }: Props) {
  const { authHeaders } = usePilotAuth();
  const [assets, setAssets] = useState<any[]>([]);
  const [name, setName] = useState('');
  const [assetType, setAssetType] = useState('contract');
  const [network, setNetwork] = useState('ethereum-mainnet');
  const [identifier, setIdentifier] = useState('');
  const [message, setMessage] = useState('');

  async function load() {
    const response = await fetch(`${apiUrl}/assets`, { headers: { ...authHeaders() } });
    if (!response.ok) return;
    const payload = await response.json();
    setAssets(payload.assets ?? []);
  }

  async function create() {
    const response = await fetch(`${apiUrl}/assets`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({
        name,
        asset_type: assetType,
        chain_network: network,
        identifier,
        risk_tier: 'medium',
        tags: []
      })
    });
    setMessage(response.ok ? 'Asset saved.' : 'Unable to save asset.');
    if (response.ok) {
      setName('');
      setIdentifier('');
      load();
    }
  }

  useEffect(() => {
    load();
  }, []);

  return (
    <div className="dataCard">
      <h1>Assets</h1>
      <p className="muted">Register real contracts, wallets, and treasury-linked assets for workspace monitoring.</p>
      <input placeholder="Asset name" value={name} onChange={(event) => setName(event.target.value)} />
      <select value={assetType} onChange={(event) => setAssetType(event.target.value)}>
        <option value="contract">Contract</option>
        <option value="wallet">Wallet</option>
        <option value="treasury-linked asset">Treasury-linked asset</option>
        <option value="oracle">Oracle</option>
        <option value="custody component">Custody component</option>
        <option value="settlement component">Settlement component</option>
        <option value="admin-controlled module">Admin-controlled module</option>
        <option value="monitored counterparty">Monitored counterparty</option>
        <option value="policy-controlled workflow object">Policy-controlled workflow object</option>
      </select>
      <input placeholder="Chain/network" value={network} onChange={(event) => setNetwork(event.target.value)} />
      <input placeholder="Contract, wallet, or identifier" value={identifier} onChange={(event) => setIdentifier(event.target.value)} />
      <button type="button" onClick={create}>Create asset</button>
      {message ? <p className="statusLine">{message}</p> : null}
      <ul>{assets.map((asset) => <li key={asset.id}>{asset.name} · {asset.asset_type} · {asset.chain_network}</li>)}</ul>
    </div>
  );
}
