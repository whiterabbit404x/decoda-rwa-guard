'use client';

import { useEffect, useMemo, useState } from 'react';

import { resolveApiUrl } from '../../dashboard-data';
import { usePilotAuth } from '../../pilot-auth-context';
import RuntimeSummaryPanel from '../../runtime-summary-panel';

type TabKey = 'targets' | 'systems';

type AssetRow = { id: string; name?: string };
type TargetRow = {
  id: string;
  name?: string;
  target_type?: string;
  provider?: string | null;
  monitoring_enabled?: boolean;
  enabled?: boolean;
  last_checked_at?: string | null;
  health_status?: string | null;
  next_action?: string | null;
  monitored_system_id?: string | null;
};

type MonitoredSystemRow = {
  id: string;
  asset_name?: string;
  target_name?: string;
  is_enabled?: boolean;
  runtime_status?: string;
  last_heartbeat?: string | null;
  last_event_at?: string | null;
  coverage_reason?: string | null;
  freshness_status?: string | null;
};

function formatTimestamp(value?: string | null): string {
  if (!value) return 'Never';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return 'Never';
  return parsed.toLocaleString();
}

export default function MonitoringSourcesPage() {
  const [activeTab, setActiveTab] = useState<TabKey>('targets');
  const [assets, setAssets] = useState<AssetRow[]>([]);
  const [targets, setTargets] = useState<TargetRow[]>([]);
  const [systems, setSystems] = useState<MonitoredSystemRow[]>([]);
  const [message, setMessage] = useState('');
  const apiUrl = resolveApiUrl();
  const { authHeaders } = usePilotAuth();

  useEffect(() => {
    async function load() {
      const [assetsResponse, targetsResponse, systemsResponse] = await Promise.all([
        fetch(`${apiUrl}/assets`, { headers: authHeaders(), cache: 'no-store' }),
        fetch(`${apiUrl}/targets`, { headers: authHeaders(), cache: 'no-store' }),
        fetch(`${apiUrl}/monitoring/systems`, { headers: authHeaders(), cache: 'no-store' }),
      ]);

      if (!assetsResponse.ok || !targetsResponse.ok || !systemsResponse.ok) {
        setMessage('Unable to load monitoring sources.');
        return;
      }

      const assetsPayload = await assetsResponse.json();
      const targetsPayload = await targetsResponse.json();
      const systemsPayload = await systemsResponse.json();
      setAssets(assetsPayload.assets ?? []);
      setTargets(targetsPayload.targets ?? []);
      setSystems(systemsPayload.systems ?? []);
      setMessage('');
    }

    void load();
  }, [apiUrl, authHeaders]);

  const targetNameById = useMemo(() => new Map(targets.map((target) => [target.id, target.name || 'Unnamed target'])), [targets]);

  const hasAssetsNoTargets = assets.length > 0 && targets.length === 0;
  const hasTargetsNoSystems = targets.length > 0 && systems.length === 0;

  return (
    <main className="productPage">
      <RuntimeSummaryPanel />
      <section className="dataCard stack">
        <h1>Monitoring Sources</h1>
        <p className="muted">Review targets and monitored systems from backend-backed workspace data.</p>
        <div className="buttonRow" role="tablist" aria-label="Monitoring sources tabs">
          <button type="button" role="tab" aria-selected={activeTab === 'targets'} onClick={() => setActiveTab('targets')}>Monitoring Targets</button>
          <button type="button" role="tab" aria-selected={activeTab === 'systems'} onClick={() => setActiveTab('systems')}>Monitored Systems</button>
        </div>

        {message ? <p className="statusLine">{message}</p> : null}

        {activeTab === 'targets' ? (
          <div className="stack compactStack" role="tabpanel">
            {hasAssetsNoTargets ? <div className="emptyStatePanel"><p>No monitoring target is linked to this asset yet.</p><a href="/monitoring-sources/targets">Create monitoring target</a></div> : null}
            <table>
              <thead>
                <tr>
                  <th>Target Name</th><th>Type</th><th>Provider</th><th>Systems</th><th>Status</th><th>Last Poll</th><th>Next Action</th>
                </tr>
              </thead>
              <tbody>
                {targets.map((target) => (
                  <tr key={target.id}>
                    <td>{target.name || 'Unnamed target'}</td>
                    <td>{target.target_type || 'Unknown'}</td>
                    <td>{target.provider || 'Default provider'}</td>
                    <td>{target.monitored_system_id ? 'Linked' : 'Unlinked'}</td>
                    <td>{target.monitoring_enabled && target.enabled ? 'Enabled' : 'Disabled'}</td>
                    <td>{formatTimestamp(target.last_checked_at)}</td>
                    <td>{target.next_action || (target.health_status === 'degraded' ? 'Enable monitored system' : 'No action required')}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="stack compactStack" role="tabpanel">
            {hasTargetsNoSystems ? <div className="emptyStatePanel"><p>Target exists, but no monitored system is enabled.</p><a href="/monitoring-sources/monitored-systems">Enable monitored system</a></div> : null}
            <table>
              <thead>
                <tr>
                  <th>System Name</th><th>Linked Target</th><th>Enabled</th><th>Runtime Status</th><th>Last Heartbeat</th><th>Last Telemetry</th><th>Coverage State</th><th>Evidence Source</th>
                </tr>
              </thead>
              <tbody>
                {systems.map((system) => (
                  <tr key={system.id}>
                    <td>{system.asset_name || `System ${system.id.slice(0, 8)}`}</td>
                    <td>{system.target_name || targetNameById.get((system as any).target_id) || 'Unlinked target'}</td>
                    <td>{system.is_enabled ? 'Yes' : 'No'}</td>
                    <td>{system.runtime_status || 'unknown'}</td>
                    <td>{formatTimestamp(system.last_heartbeat)}</td>
                    <td>{formatTimestamp(system.last_event_at)}</td>
                    <td>{system.coverage_reason || 'pending'}</td>
                    <td>{system.freshness_status ? `backend:${system.freshness_status}` : 'backend:unavailable'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </main>
  );
}
