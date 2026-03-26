'use client';

import { useEffect, useState } from 'react';

import { usePilotAuth } from './pilot-auth-context';

type Props = { apiUrl: string };

export default function TargetsManager({ apiUrl }: Props) {
  const { authHeaders } = usePilotAuth();
  const [targets, setTargets] = useState<any[]>([]);
  const [name, setName] = useState('');
  const [targetType, setTargetType] = useState('contract');
  const [network, setNetwork] = useState('ethereum');
  const [wallet, setWallet] = useState('');
  const [message, setMessage] = useState('');

  async function load() {
    const response = await fetch(`${apiUrl}/targets`, { headers: { ...authHeaders() } });
    if (!response.ok) return;
    const payload = await response.json();
    setTargets(payload.targets ?? []);
  }

  async function create() {
    const response = await fetch(`${apiUrl}/targets`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ name, target_type: targetType, chain_network: network, wallet_address: wallet || undefined, enabled: true, severity_preference: 'medium', tags: [] })
    });
    setMessage(response.ok ? 'Target saved.' : 'Unable to save target.');
    if (response.ok) {
      setName('');
      setWallet('');
      load();
    }
  }

  useEffect(() => { load(); }, []);

  return (
    <div className="dataCard">
      <h1>Targets</h1>
      <p className="muted">Workspace-scoped customer assets and monitored actors.</p>
      <input placeholder="Target name" value={name} onChange={(event) => setName(event.target.value)} />
      <select value={targetType} onChange={(event) => setTargetType(event.target.value)}>
        <option value="contract">Contract</option><option value="wallet">Wallet</option><option value="oracle">Oracle</option><option value="treasury-linked asset">Treasury-linked asset</option><option value="settlement component">Settlement component</option><option value="admin-controlled module">Admin-controlled module</option>
      </select>
      <input placeholder="Chain/network" value={network} onChange={(event) => setNetwork(event.target.value)} />
      <input placeholder="Wallet (0x...)" value={wallet} onChange={(event) => setWallet(event.target.value)} />
      <button type="button" onClick={create}>Create target</button>
      {message ? <p className="statusLine">{message}</p> : null}
      <ul>{targets.map((target) => <li key={target.id}>{target.name} · {target.target_type} · {target.chain_network}</li>)}</ul>
    </div>
  );
}
