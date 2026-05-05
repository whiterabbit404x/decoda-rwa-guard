'use client';

import { useEffect, useMemo, useState } from 'react';
import { usePilotAuth } from '../../pilot-auth-context';
import RuntimeSummaryPanel from '../../runtime-summary-panel';
import { TableShell, TabStrip } from '../../components/ui-primitives';

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';
const REQUIRED_INCLUDES = [
  'telemetry snapshot',
  'detection event',
  'alert',
  'incident timeline',
  'response action',
  'audit log'
];

type ExportJob = {
  id: string;
  export_type?: string;
  format?: string;
  status?: string;
  created_at?: string;
  incident_id?: string;
  evidence_source?: string;
  size_bytes?: number;
  package_ready?: boolean;
  download_url?: string | null;
};

export default function ExportsPage() {
  const { authHeaders } = usePilotAuth();
  const [jobs, setJobs] = useState<ExportJob[]>([]);
  const [message, setMessage] = useState('');
  const [activeTab, setActiveTab] = useState<'packages' | 'audit'>('packages');
  const [auditRows, setAuditRows] = useState<any[]>([]);
  const [auditUnavailable, setAuditUnavailable] = useState('');

  async function loadJobs() {
    const response = await fetch(`${API_URL}/exports`, { headers: authHeaders(), cache: 'no-store' });
    const payload = response.ok ? await response.json() : { exports: [] };
    setJobs(payload.exports ?? []);
  }

  async function loadAuditLogs() {
    const response = await fetch(`${API_URL}/events`, { headers: authHeaders(), cache: 'no-store' });
    if (!response.ok) {
      setAuditRows([]);
      setAuditUnavailable('Audit log feed unavailable from the current workspace endpoint.');
      return;
    }
    const payload = await response.json();
    setAuditRows(payload.events ?? payload.audit_logs ?? []);
    setAuditUnavailable('');
  }

  async function createExport(type: 'history' | 'alerts' | 'findings' | 'report', format: 'csv' | 'json') {
    const response = await fetch(`${API_URL}/exports/${type}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ format })
    });
    const payload = await response.json();
    setMessage(response.ok ? `Evidence package ${payload.status}.` : payload.detail ?? 'Evidence package export failed.');
    await loadJobs();
  }

  useEffect(() => {
    void loadJobs();
    void loadAuditLogs();
  }, []);

  const evidenceRows = useMemo(() => jobs.map((job) => {
    const includes = REQUIRED_INCLUDES.join(', ');
    const size = typeof job.size_bytes === 'number' ? `${(job.size_bytes / 1024).toFixed(1)} KB` : 'Pending';
    const ready = Boolean(job.download_url || job.package_ready);
    return {
      packageId: job.id,
      incident: job.incident_id ?? `INC-${job.id?.slice(0, 6) ?? 'N/A'}`,
      dateCreated: job.created_at ? new Date(job.created_at).toLocaleString() : 'Pending',
      includes,
      size,
      evidenceSource: job.evidence_source ?? `${job.export_type ?? 'export'} ${job.format ?? ''}`.trim(),
      ready,
      downloadUrl: job.download_url,
      status: job.status ?? 'pending'
    };
  }), [jobs]);

  return <main className="productPage">
      <RuntimeSummaryPanel /><section className="dataCard"><h1>Evidence &amp; Audit</h1><p className="muted">Create evidence packages from persisted workspace data and review audit trail events.</p>

      <TabStrip
        tabs={[{ key: 'packages', label: 'Evidence Packages' }, { key: 'audit', label: 'Audit Logs' }]}
        active={activeTab}
        onChange={(key) => setActiveTab(key as 'packages' | 'audit')}
      />

      {activeTab === 'packages' ? <>
        <div className="buttonRow">
          <button type="button" onClick={() => createExport('history', 'csv')} className="primary">Create Evidence Package</button>
          <button type="button" onClick={() => createExport('alerts', 'json')}>Alerts JSON</button>
          <button type="button" onClick={() => createExport('findings', 'csv')}>Findings CSV</button>
        </div>

        <p className="tableMeta">Required includes: {REQUIRED_INCLUDES.join(' · ')}</p>
        {message ? <p className="statusLine">{message}</p> : null}

        <TableShell headers={['Package ID', 'Incident', 'Date Created', 'Includes', 'Size', 'Evidence Source', 'Actions']}>
          {evidenceRows.map((row) => <tr key={row.packageId}>
              <td>{row.packageId}</td>
              <td>{row.incident}</td>
              <td>{row.dateCreated}</td>
              <td>{row.includes}</td>
              <td>{row.size}</td>
              <td>{row.evidenceSource}</td>
              <td>
                <button type="button" disabled={!row.ready} onClick={() => createExport('report', 'json')}>Export</button>{' '}
                <a href={row.downloadUrl ? `${API_URL}${row.downloadUrl}` : undefined} aria-disabled={!row.ready} onClick={(event) => {
                  if (!row.ready) event.preventDefault();
                }}>
                  <button type="button" disabled={!row.ready}>Download</button>
                </a>
              </td>
            </tr>)}
        </TableShell>
      </> : <>
        {auditUnavailable ? <p className="statusLine">{auditUnavailable}</p> : null}
        <TableShell headers={['Timestamp', 'Actor/System', 'Action', 'Target', 'Result', 'Source']}>
          {auditRows.length > 0 ? auditRows.map((row, index) => <tr key={`${row.id ?? row.timestamp ?? index}`}>
              <td>{row.timestamp ?? row.created_at ?? 'n/a'}</td>
              <td>{row.actor ?? row.system ?? 'system'}</td>
              <td>{row.action ?? row.event_type ?? 'n/a'}</td>
              <td>{row.target ?? row.target_id ?? 'n/a'}</td>
              <td>{row.result ?? row.status ?? 'n/a'}</td>
              <td>{row.source ?? row.origin ?? 'workspace feed'}</td>
            </tr>) : <tr><td colSpan={6}>{auditUnavailable || 'No audit logs available yet.'}</td></tr>}
        </TableShell>
      </>}
  </section></main>;
}
