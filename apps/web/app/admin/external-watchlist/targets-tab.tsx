'use client';

import { useState } from 'react';

import { StatusPill } from 'app/components/ui-primitives';
import { usePilotAuth } from 'app/pilot-auth-context';

import { sendJson, watchlistUrl } from './external-watchlist-api';
import type { ExternalWatchlistFeature } from './external-watchlist-console';
import type { WatchlistDetail, WatchlistTarget } from './external-watchlist-types';
import {
  BACKFILL_OPTIONS,
  TARGET_TYPE_OPTIONS,
  apiErrorMessage,
  backfillProgressLabel,
  backfillStatusLabel,
  clampPercent,
  formatRelativeTime,
  formatTimestamp,
  isEvmAddress,
} from './external-watchlist-view';

type Diagnostic = {
  target_id: string;
  checked_at: string;
  checks: Array<{ check: string; ok: boolean; message?: string; value?: unknown; block?: number }>;
  result: { ok: boolean; contract_type?: string; chain_tip?: number; error?: { message?: string } };
};

const CHECK_LABELS: Record<string, string> = {
  rpc_configured: 'RPC endpoint configured',
  chain_id_matches: 'Endpoint serves the expected chain',
  chain_tip_readable: 'Chain tip readable',
  bytecode_present: 'Bytecode present at address',
  processing_lag_blocks: 'Live tail processing lag (blocks)',
};

/**
 * Targets of one protocol. Every control here is read-only toward the chain:
 * edit a label, pause or resume READING, remove, inspect, open the explorer,
 * run a read-only diagnostic, or queue a historical scan. There is no response
 * action, and the backend refuses one if asked (execution_authority = NONE).
 */
export default function TargetsTab({ watchlistId, detail, feature, onChanged, onInspectEvents }: {
  watchlistId: string;
  detail: WatchlistDetail;
  feature: ExternalWatchlistFeature;
  onChanged: () => Promise<void> | void;
  onInspectEvents: (targetId: string) => void;
}) {
  const { authHeaders, csrfReady, refreshCsrfToken } = usePilotAuth();
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [editing, setEditing] = useState<{ id: string; label: string } | null>(null);
  const [diagnostic, setDiagnostic] = useState<Diagnostic | null>(null);
  const [adding, setAdding] = useState(false);
  const [confirmRemove, setConfirmRemove] = useState<string | null>(null);
  const networks = feature.networks ?? [];
  const [form, setForm] = useState({
    network: detail.targets[0]?.network.key ?? networks.find((n) => n.rpc_configured)?.key ?? '',
    address: '',
    label: '',
    target_type: 'contract',
    backfill_days: 30,
  });

  async function run(targetId: string | null, label: string, request: () => ReturnType<typeof sendJson>, success: string) {
    setBusyId(targetId ?? 'new');
    setError(null);
    setMessage(null);
    try {
      const result = await request();
      if (!result.ok) {
        setError(apiErrorMessage(result.status, result.payload, `${label} failed`));
        return null;
      }
      setMessage(success);
      await onChanged();
      return result.payload;
    } catch {
      setError(`${label} failed.`);
      return null;
    } finally {
      setBusyId(null);
    }
  }

  async function copy(address: string) {
    try {
      await navigator.clipboard.writeText(address);
      setMessage('Address copied.');
    } catch {
      setMessage(address);
    }
  }

  const disabled = (id: string) => !csrfReady || busyId === id;

  return (
    <section data-testid="targets-tab">
      <div className="ewlSectionHeader">
        <p className="ewlReadOnlyNote">
          Read-only monitoring. External targets have <strong>execution authority: none</strong> — Decoda never executes,
          signs, approves, pauses or remediates anything on them.
        </p>
        <button type="button" className="btn btn-secondary" disabled={!csrfReady} onClick={() => setAdding((value) => !value)}>
          {adding ? 'Cancel' : '+ Add target'}
        </button>
      </div>

      {adding ? (
        <form
          className="dataCard ewlInlineForm"
          data-testid="add-target-form"
          onSubmit={(event) => {
            event.preventDefault();
            if (!isEvmAddress(form.address)) {
              setError('Enter a 0x-prefixed, 40-hex-character EVM address.');
              return;
            }
            void run(null, 'Add target', () => sendJson(watchlistUrl(watchlistId, '/targets'), 'POST', {
              network: form.network, address: form.address.trim(), label: form.label.trim() || undefined,
              target_type: form.target_type, backfill_days: form.backfill_days,
            }, authHeaders, refreshCsrfToken), 'Target added.').then((payload) => {
              if (payload) {
                setAdding(false);
                setForm((previous) => ({ ...previous, address: '', label: '' }));
              }
            });
          }}
        >
          <label>
            Network
            <select value={form.network} onChange={(event) => setForm({ ...form, network: event.target.value })}>
              {networks.map((network) => (
                <option key={network.key} value={network.key} disabled={!network.rpc_configured}>
                  {network.label}{network.rpc_configured ? '' : ' — no RPC endpoint'}
                </option>
              ))}
            </select>
          </label>
          <label className="ewlGrow">
            Address
            <input className="ewlMono" value={form.address} placeholder="0x…" spellCheck={false}
              onChange={(event) => setForm({ ...form, address: event.target.value })} />
          </label>
          <label>
            Label
            <input value={form.label} maxLength={120} onChange={(event) => setForm({ ...form, label: event.target.value })} />
          </label>
          <label>
            Type
            <select value={form.target_type} onChange={(event) => setForm({ ...form, target_type: event.target.value })}>
              {TARGET_TYPE_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
            </select>
          </label>
          <label>
            Backfill
            <select value={String(form.backfill_days)} onChange={(event) => setForm({ ...form, backfill_days: Number(event.target.value) })}>
              {BACKFILL_OPTIONS.map((option) => <option key={option.value} value={String(option.value)}>{option.label}</option>)}
            </select>
          </label>
          <button type="submit" className="btn btn-primary" disabled={!csrfReady || busyId === 'new'}>Start Monitoring</button>
        </form>
      ) : null}

      {message ? <p className="statusLine ewlSuccess" role="status">{message}</p> : null}
      {error ? <p className="statusLine" style={{ color: 'var(--danger-fg)' }} role="alert">{error}</p> : null}

      {detail.targets.length === 0 ? (
        <p className="muted">No targets yet. Add a public contract, wallet, multisig or oracle address to start monitoring.</p>
      ) : (
        <div className="adminTableWrap">
          <table className="adminTable ewlTable">
            <thead>
              <tr>
                <th>Label</th>
                <th>Address</th>
                <th>Type</th>
                <th>Network</th>
                <th>Monitoring</th>
                <th>Last block</th>
                <th>Last poll</th>
                <th>Backfill</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {detail.targets.map((target) => (
                <TargetRow
                  key={target.id}
                  target={target}
                  editing={editing?.id === target.id ? editing : null}
                  disabled={disabled(target.id)}
                  confirmRemove={confirmRemove === target.id}
                  onEdit={(label) => setEditing({ id: target.id, label })}
                  onEditChange={(label) => setEditing({ id: target.id, label })}
                  onEditCancel={() => setEditing(null)}
                  onEditSave={() => {
                    const label = editing?.label ?? '';
                    void run(target.id, 'Rename', () => sendJson(watchlistUrl(watchlistId, `/targets/${target.id}`), 'PATCH',
                      { label }, authHeaders, refreshCsrfToken), 'Label saved.').then(() => setEditing(null));
                  }}
                  onToggle={() => void run(target.id, target.monitoring_enabled ? 'Pause' : 'Resume',
                    () => sendJson(watchlistUrl(watchlistId, `/targets/${target.id}`), 'PATCH',
                      { monitoring_enabled: !target.monitoring_enabled }, authHeaders, refreshCsrfToken),
                    target.monitoring_enabled ? 'Target paused.' : 'Target resumed.')}
                  onRemoveAsk={() => setConfirmRemove(target.id)}
                  onRemoveCancel={() => setConfirmRemove(null)}
                  onRemove={() => void run(target.id, 'Remove', () => sendJson(watchlistUrl(watchlistId, `/targets/${target.id}`),
                    'DELETE', {}, authHeaders, refreshCsrfToken), 'Target removed. Its history is kept.').then(() => setConfirmRemove(null))}
                  onCopy={() => void copy(target.address)}
                  onInspect={() => onInspectEvents(target.id)}
                  onDiagnostic={() => void run(target.id, 'Diagnostic', () => sendJson(
                    watchlistUrl(watchlistId, `/targets/${target.id}/diagnostic`), 'POST', {}, authHeaders, refreshCsrfToken,
                  ), 'Diagnostic complete.').then((payload) => { if (payload) setDiagnostic(payload as unknown as Diagnostic); })}
                  onBackfill={() => void run(target.id, 'Backfill', () => sendJson(watchlistUrl(watchlistId, '/backfill'), 'POST',
                    { days: 30, target_ids: [target.id] }, authHeaders, refreshCsrfToken), 'Historical backfill queued for this target.')}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {diagnostic ? (
        <article className="dataCard ewlDiagnostic" data-testid="target-diagnostic">
          <div className="ewlSectionHeader">
            <p className="sectionEyebrow">Read-only diagnostic · {formatTimestamp(diagnostic.checked_at)}</p>
            <button type="button" className="btn btn-ghost" onClick={() => setDiagnostic(null)}>Close</button>
          </div>
          <ul className="ewlChecks">
            {diagnostic.checks.map((check) => (
              <li key={check.check}>
                <StatusPill label={check.ok ? 'Pass' : 'Fail'} variant={check.ok ? 'success' : 'danger'} />{' '}
                {CHECK_LABELS[check.check] ?? check.check}
                {check.value !== undefined && check.value !== null ? <span className="muted"> · {String(check.value)}</span> : null}
                {check.block ? <span className="muted"> · block #{check.block.toLocaleString('en-US')}</span> : null}
                {check.message ? <span className="muted"> · {check.message}</span> : null}
              </li>
            ))}
          </ul>
          {diagnostic.result.contract_type ? (
            <p className="muted ewlSmall">Detected: {diagnostic.result.contract_type.replace(/_/g, ' ')}</p>
          ) : null}
        </article>
      ) : null}
    </section>
  );
}

function TargetRow(props: {
  target: WatchlistTarget;
  editing: { id: string; label: string } | null;
  disabled: boolean;
  confirmRemove: boolean;
  onEdit: (label: string) => void;
  onEditChange: (label: string) => void;
  onEditCancel: () => void;
  onEditSave: () => void;
  onToggle: () => void;
  onRemoveAsk: () => void;
  onRemoveCancel: () => void;
  onRemove: () => void;
  onCopy: () => void;
  onInspect: () => void;
  onDiagnostic: () => void;
  onBackfill: () => void;
}) {
  const { target } = props;
  const backfill = target.backfill;
  return (
    <tr data-testid="target-row">
      <td>
        {props.editing ? (
          <span className="ewlInlineControl">
            <input value={props.editing.label} maxLength={120} aria-label="Target label"
              onChange={(event) => props.onEditChange(event.target.value)} />
            <button type="button" className="btn btn-primary" disabled={props.disabled} onClick={props.onEditSave}>Save</button>
            <button type="button" className="btn btn-ghost" onClick={props.onEditCancel}>Cancel</button>
          </span>
        ) : (
          <>
            {target.label ?? <span className="muted">Unlabelled</span>}
            <button type="button" className="ewlLinkButton" onClick={() => props.onEdit(target.label ?? '')}>Edit</button>
          </>
        )}
      </td>
      <td>
        <span className="ewlMono" title={target.address}>{target.address}</span>
        <button type="button" className="ewlLinkButton" onClick={props.onCopy} aria-label="Copy address">Copy</button>
      </td>
      <td>
        {target.target_type}
        {target.contract_type ? <span className="adminContactMembers">{target.contract_type.replace(/_/g, ' ')}</span> : null}
      </td>
      <td>{target.network.short_label}</td>
      <td>
        <StatusPill label={target.monitoring_enabled ? 'Active' : 'Paused'} variant={target.monitoring_enabled ? 'success' : 'neutral'} />
        {target.consecutive_failures > 0 ? (
          <span className="adminContactMembers" title={target.last_poll_error ?? undefined}>
            {target.consecutive_failures} failed poll(s)
          </span>
        ) : null}
      </td>
      <td>{target.last_processed_block != null ? `#${target.last_processed_block.toLocaleString('en-US')}` : '— not yet'}</td>
      <td title={formatTimestamp(target.last_successful_poll_at)}>{formatRelativeTime(target.last_successful_poll_at)}</td>
      <td>
        {backfill ? (
          <span className="ewlInlineProgress">
            <span>{backfillStatusLabel(backfill.status)}</span>
            {backfill.status === 'running' || backfill.status === 'pending' ? (
              <span className="ewlProgress" aria-hidden="true">
                <span className="ewlProgressFill" style={{ width: `${clampPercent(backfill.percent)}%` }} />
              </span>
            ) : null}
            <span className="adminContactMembers">{backfillProgressLabel(backfill)}</span>
          </span>
        ) : (
          <span className="muted">None</span>
        )}
      </td>
      <td>
        <div className="adminRowActions">
          <button type="button" className="btn btn-secondary" onClick={props.onInspect}>Decoded events</button>
          {target.explorer_url ? (
            <a className="btn btn-secondary" href={target.explorer_url} target="_blank" rel="noopener noreferrer nofollow">Explorer ↗</a>
          ) : null}
          <button type="button" className="btn btn-secondary" disabled={props.disabled} onClick={props.onDiagnostic}>Diagnostic</button>
          <button type="button" className="btn btn-secondary" disabled={props.disabled} onClick={props.onBackfill}>Backfill 30d</button>
          <button type="button" className="btn btn-secondary" disabled={props.disabled} onClick={props.onToggle}>
            {target.monitoring_enabled ? 'Pause' : 'Resume'}
          </button>
          {props.confirmRemove ? (
            <>
              <button type="button" className="btn btn-danger" disabled={props.disabled} onClick={props.onRemove}>Confirm remove</button>
              <button type="button" className="btn btn-ghost" onClick={props.onRemoveCancel}>Keep</button>
            </>
          ) : (
            <button type="button" className="btn btn-ghost" disabled={props.disabled} onClick={props.onRemoveAsk}>Remove</button>
          )}
        </div>
      </td>
    </tr>
  );
}
