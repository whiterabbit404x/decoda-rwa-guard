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

type ReconcileSummary = {
  targets_scanned: number;
  created_or_updated: number;
  invalid_reasons: Record<string, number>;
  skipped_reasons: Record<string, number>;
  repaired_monitored_system_ids: string[];
};

function formatReasonCounts(label: string, reasons: Record<string, number>): string {
  const entries = Object.entries(reasons);
  if (!entries.length) {
    return `${label}: none`;
  }
  const details = entries.map(([reason, count]) => `${reason} (${count})`).join(', ');
  return `${label}: ${details}`;
}

export default function MonitoredSystemsManager({ apiUrl }: Props) {
  const { authHeaders } = usePilotAuth();
  const [systems, setSystems] = useState<SystemRow[]>([]);
  const [message, setMessage] = useState('');
  const [isReconciling, setIsReconciling] = useState(false);
  const [reconcileSummary, setReconcileSummary] = useState<ReconcileSummary | null>(null);

  async function load() {
    const response = await fetch(`${apiUrl}/monitoring/systems`, { headers: authHeaders(), cache: 'no-store' });
    if (!response.ok) {
      setMessage('Unable to load monitored systems.');
      return null;
    }
    const payload = await response.json();
    const loadedSystems = payload.systems ?? [];
    setSystems(loadedSystems);
    return loadedSystems;
  }

  async function runReconcile() {
    setMessage('');
    setIsReconciling(true);
    try {
      const response = await fetch(`${apiUrl}/monitoring/systems/reconcile`, {
        method: 'POST',
        headers: authHeaders(),
      });
      if (!response.ok) {
        setMessage('Unable to repair monitored systems.');
        return;
      }

      const payload = await response.json();
      const summary: ReconcileSummary = {
        targets_scanned: Number(payload?.reconcile?.targets_scanned ?? 0),
        created_or_updated: Number(payload?.reconcile?.created_or_updated ?? 0),
        invalid_reasons: payload?.reconcile?.invalid_reasons ?? {},
        skipped_reasons: payload?.reconcile?.skipped_reasons ?? {},
        repaired_monitored_system_ids: payload?.reconcile?.repaired_monitored_system_ids ?? [],
      };
      setReconcileSummary(summary);
      const reconciledSystems = Array.isArray(payload?.systems) ? payload.systems : null;
      if (reconciledSystems) {
        setSystems(reconciledSystems);
      }
      const reloadedSystems = await load();
      const visibleSystems = reloadedSystems ?? reconciledSystems ?? [];
      if (summary.created_or_updated > 0 && visibleSystems.length === 0) {
        setMessage('Repair reported success, but no monitored systems were visible after reload.');
        return;
      }

      setMessage(
        `Repair completed. ${summary.created_or_updated} monitored systems created or updated from ${summary.targets_scanned} targets scanned.`,
      );
    } finally {
      setIsReconciling(false);
    }
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

  useEffect(() => {
    void load();
  }, []);

  return (
    <section className="dataCard stack compactStack">
      <h1>Monitored Systems</h1>
      <p className="muted">Bridge assets to runtime monitoring through target-linked monitored systems.</p>
      <div className="buttonRow">
        <button type="button" onClick={() => void runReconcile()} disabled={isReconciling}>
          {isReconciling ? 'Repairing monitored systems…' : 'Repair monitored systems'}
        </button>
      </div>
      {systems.length === 0 ? (
        <p className="muted">
          No monitored systems yet. If you already have enabled targets, use Repair monitored systems to backfill missing links.
        </p>
      ) : null}
      {reconcileSummary ? (
        <div className="stack compactStack">
          <p className="tableMeta">
            Reconcile summary: scanned {reconcileSummary.targets_scanned} targets, created/updated {reconcileSummary.created_or_updated}, repaired IDs{' '}
            {reconcileSummary.repaired_monitored_system_ids.length}
          </p>
          <p className="tableMeta">{formatReasonCounts('Invalid reasons', reconcileSummary.invalid_reasons)}</p>
          <p className="tableMeta">{formatReasonCounts('Skipped reasons', reconcileSummary.skipped_reasons)}</p>
        </div>
      ) : null}
      {systems.map((system) => (
        <article key={system.id} className="overviewListItem">
          <div>
            <p>
              <strong>{system.asset_name || system.asset_id}</strong> → {system.target_name || system.target_id}
            </p>
            <p className="tableMeta">
              {system.chain || 'unknown chain'} · Config: {system.is_enabled ? 'Enabled' : 'Disabled'} · Runtime: {system.runtime_status}
              {' · '}
              Last heartbeat: {system.last_heartbeat ? new Date(system.last_heartbeat).toLocaleString() : 'Never'}
            </p>
            {system.last_error_text ? <p className="tableMeta">Last error: {system.last_error_text}</p> : null}
          </div>
          <div className="buttonRow">
            <button type="button" onClick={() => void toggle(system)}>
              {system.is_enabled ? 'Disable' : 'Enable'}
            </button>
            <button type="button" onClick={() => void remove(system.id)}>
              Delete
            </button>
          </div>
        </article>
      ))}
      {message ? <p className="statusLine">{message}</p> : null}
    </section>
  );
}
