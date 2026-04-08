'use client';

import { useEffect, useState } from 'react';

import { usePilotAuth } from './pilot-auth-context';

type Props = { apiUrl: string };

type SystemRow = {
  id: string;
  asset_id: string;
  target_id: string;
  asset_name?: string;
  target_name?: string;
  chain?: string;
  is_enabled: boolean;
  runtime_status: 'active' | 'idle' | 'degraded' | 'error' | 'offline';
  last_heartbeat?: string | null;
  last_error_text?: string | null;
};

export default function MonitoredSystemsManager({ apiUrl }: Props) {
  const { authHeaders } = usePilotAuth();
  const [systems, setSystems] = useState<SystemRow[]>([]);
  const [message, setMessage] = useState('');

  async function load() {
    const response = await fetch(`${apiUrl}/monitoring/systems`, { headers: authHeaders(), cache: 'no-store' });
    if (!response.ok) {
      setMessage('Unable to load monitored systems.');
      return;
    }
    const payload = await response.json();
    setSystems(payload.systems ?? []);
  }

  async function toggle(system: SystemRow) {
    setMessage('');
    const enabled = !system.is_enabled;
    const response = await fetch(`${apiUrl}/monitoring/systems/${system.id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ enabled }),
    });
    if (!response.ok) {
      setMessage('Unable to update system status.');
      return;
    }
    await load();
    setMessage(enabled ? 'Monitoring enabled for this target.' : 'Monitoring disabled for this target.');
  }

  async function remove(systemId: string) {
    const response = await fetch(`${apiUrl}/monitoring/systems/${systemId}`, { method: 'DELETE', headers: authHeaders() });
    if (!response.ok) {
      setMessage('Unable to delete monitored system.');
      return;
    }
    setMessage('Monitored system deleted.');
    void load();
  }

  useEffect(() => { void load(); }, []);

  return (
    <section className="dataCard stack compactStack">
      <h1>Monitored Systems</h1>
      <p className="muted">Bridge assets to runtime monitoring through target-linked monitored systems.</p>
      {systems.length === 0 ? <p className="muted">No monitored systems yet. Enable monitoring on a target to create one.</p> : null}
      {systems.map((system) => (
        <article key={system.id} className="overviewListItem">
          <div>
            <p><strong>{system.asset_name || system.asset_id}</strong> → {system.target_name || system.target_id}</p>
            <p className="tableMeta">
              {system.chain || 'unknown chain'} · Config: {system.is_enabled ? 'Enabled' : 'Disabled'} · Runtime: {system.runtime_status}
              {' · '}
              Last heartbeat: {system.last_heartbeat ? new Date(system.last_heartbeat).toLocaleString() : 'Never'}
            </p>
            {system.last_error_text ? <p className="tableMeta">Last error: {system.last_error_text}</p> : null}
          </div>
          <div className="buttonRow">
            <button type="button" onClick={() => void toggle(system)}>{system.is_enabled ? 'Disable' : 'Enable'}</button>
            <button type="button" onClick={() => void remove(system.id)}>Delete</button>
          </div>
        </article>
      ))}
      {message ? <p className="statusLine">{message}</p> : null}
    </section>
  );
}
