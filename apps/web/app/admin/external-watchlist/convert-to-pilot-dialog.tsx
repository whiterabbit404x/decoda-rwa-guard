'use client';

import { useEffect, useState } from 'react';

import { usePilotAuth } from 'app/pilot-auth-context';

import { sendJson, watchlistUrl } from './external-watchlist-api';
import type { WatchlistTarget } from './external-watchlist-types';
import { apiErrorMessage, formatTimestamp, shortAddress } from './external-watchlist-view';

type ConversionResult = {
  workspace: { id: string; name: string | null };
  organization: { id: string; name: string | null; plan: string };
  evaluation: { expires_at: string | null } | null;
  evaluation_days: number;
  copied_targets: Array<{ address: string; network: string; workspace_target_type: string }>;
  next_steps: string[];
};

/**
 * "Convert to Pilot Workspace" — an explicit, confirmed founder action.
 *
 * States plainly what happens (a Pilot workspace is created, the selected
 * targets are copied as INACTIVE drafts, a 30-day evaluation starts) and what
 * does not (no invitation is sent, no monitoring starts in the workspace, and
 * the organization is not recorded as having authorized anything). The server
 * refuses the request without `confirm: true`.
 */
export default function ConvertToPilotDialog({ watchlistId, protocolName, targets, evaluationDays, onClose, onConverted }: {
  watchlistId: string;
  protocolName: string;
  targets: WatchlistTarget[];
  evaluationDays: number;
  onClose: () => void;
  onConverted: () => void;
}) {
  const { authHeaders, csrfReady, refreshCsrfToken } = usePilotAuth();
  const [selected, setSelected] = useState<string[]>(() => targets.map((target) => target.id));
  const [workspaceName, setWorkspaceName] = useState(protocolName);
  const [acknowledged, setAcknowledged] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ConversionResult | null>(null);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose();
    }
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  async function convert() {
    setBusy(true);
    setError(null);
    try {
      const response = await sendJson(watchlistUrl(watchlistId, '/convert-to-pilot'), 'POST', {
        confirm: true,
        target_ids: selected,
        workspace_name: workspaceName.trim() || undefined,
      }, authHeaders, refreshCsrfToken);
      if (!response.ok) {
        setError(apiErrorMessage(response.status, response.payload, 'Conversion failed'));
        return;
      }
      setResult(response.payload.conversion as ConversionResult);
      onConverted();
    } catch {
      setError('Conversion failed.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modalOverlay" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <div className="modalCard ewlDialog" role="dialog" aria-modal="true" aria-labelledby="ewl-convert-title" data-testid="convert-to-pilot-dialog">
        <div className="ewlDialogHeader">
          <div>
            <p className="sectionEyebrow">External Watchlist → Pilot</p>
            <h2 id="ewl-convert-title" className="ewlDialogTitle">Convert to Pilot Workspace</h2>
          </div>
          <button type="button" className="btn btn-ghost" onClick={onClose}>Close</button>
        </div>

        {result ? (
          <div data-testid="conversion-result">
            <p>
              Pilot workspace <strong>{result.workspace.name ?? protocolName}</strong> was created with a{' '}
              {result.evaluation_days}-day evaluation
              {result.evaluation?.expires_at ? ` ending ${formatTimestamp(result.evaluation.expires_at).slice(0, 10)}` : ''}.
              {' '}{result.copied_targets.length} target(s) were copied as inactive drafts.
            </p>
            <ul className="ewlSteps">
              {result.next_steps.map((step) => <li key={step}>{step}</li>)}
            </ul>
            <div className="ewlDialogActions">
              <button type="button" className="btn btn-primary" onClick={onClose}>Done</button>
            </div>
          </div>
        ) : (
          <>
            <ol className="ewlSteps">
              <li>Creates a new Pilot workspace for <strong>{protocolName}</strong>, with you as its initial owner. Your own active workspace does not change.</li>
              <li>Copies the selected public targets into it as <strong>inactive drafts</strong>. No monitoring starts in that workspace until the workspace enables it.</li>
              <li>Sets a {evaluationDays}-day Pilot evaluation using the standard Pilot lifecycle (adjustable later in Pilot Management).</li>
              <li><strong>Does not invite anyone.</strong> Invite the customer separately from the new workspace&apos;s team settings.</li>
              <li>Keeps this protocol&apos;s external monitoring history. The organization is <strong>not</strong> recorded as having authorized anything.</li>
            </ol>

            <fieldset className="ewlProfiles">
              <legend>Targets to copy</legend>
              {targets.length === 0 ? <p className="muted ewlSmall">No targets to copy. The workspace will be created empty.</p> : null}
              <div className="ewlProfileGrid">
                {targets.map((target) => (
                  <label key={target.id} className="ewlCheck">
                    <input type="checkbox" checked={selected.includes(target.id)}
                      onChange={() => setSelected((previous) => previous.includes(target.id)
                        ? previous.filter((id) => id !== target.id) : [...previous, target.id])} />
                    <span>{target.label ?? shortAddress(target.address)} · {target.target_type} · {target.network.short_label}</span>
                  </label>
                ))}
              </div>
              <p className="muted ewlSmall">Pilot plan limits apply to the copied targets.</p>
            </fieldset>

            <div className="formField">
              <label htmlFor="ewl-workspace-name">Workspace name</label>
              <input id="ewl-workspace-name" value={workspaceName} maxLength={120} onChange={(event) => setWorkspaceName(event.target.value)} />
            </div>

            <label className="ewlCheck ewlAck">
              <input type="checkbox" checked={acknowledged} onChange={(event) => setAcknowledged(event.target.checked)}
                data-testid="convert-acknowledge" />
              <span>I understand this creates a Pilot workspace, does not notify or invite the organization, and does not mark it as customer-authorized.</span>
            </label>

            {error ? <p className="ewlFormError" role="alert">{error}</p> : null}
            <div className="ewlDialogActions">
              <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
              <button type="button" className="btn btn-primary" disabled={!csrfReady || busy || !acknowledged}
                data-testid="confirm-convert" onClick={() => void convert()}>
                Convert to Pilot Workspace
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
