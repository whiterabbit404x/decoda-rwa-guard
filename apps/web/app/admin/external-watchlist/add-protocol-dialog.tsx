'use client';

import { useEffect, useRef, useState } from 'react';

import type { ConsoleNetwork } from './external-watchlist-console';
import {
  BACKFILL_OPTIONS,
  DETECTION_PROFILE_FALLBACK,
  READ_ONLY_NOTE,
  TARGET_TYPE_OPTIONS,
  buildCreatePayload,
  emptyProtocolForm,
  validateProtocolForm,
  type ProtocolForm,
} from './external-watchlist-view';

type Props = {
  networks: ConsoleNetwork[];
  profiles: Array<{ key: string; label: string }> | undefined;
  submitting: boolean;
  serverError: string | null;
  canSubmit: boolean;
  onSubmit: (payload: Record<string, unknown>) => void;
  onClose: () => void;
};

/**
 * "+ Monitor Public Protocol".
 *
 * Asks only for public facts: a name, a website, a network, a public address
 * and what to watch. There is no field for a key, a signature, a signer, or a
 * wallet connection — and the backend refuses a request that carries one.
 * The server re-validates everything (address syntax, supported chain,
 * duplicate target, RPC availability, bytecode on the chosen network).
 */
export default function AddProtocolDialog({ networks, profiles, submitting, serverError, canSubmit, onSubmit, onClose }: Props) {
  const profileOptions = profiles && profiles.length > 0 ? profiles : DETECTION_PROFILE_FALLBACK;
  const firstUsable = networks.find((network) => network.rpc_configured)?.key ?? '';
  const [form, setForm] = useState<ProtocolForm>(() => emptyProtocolForm(profileOptions, firstUsable));
  const [errors, setErrors] = useState<Record<string, string>>({});
  const nameRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    nameRef.current?.focus();
    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose();
    }
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  function update<K extends keyof ProtocolForm>(key: K, value: ProtocolForm[K]) {
    setForm((previous) => ({ ...previous, [key]: value }));
  }

  function toggleProfile(key: string) {
    setForm((previous) => ({
      ...previous,
      detection_profiles: previous.detection_profiles.includes(key)
        ? previous.detection_profiles.filter((item) => item !== key)
        : [...previous.detection_profiles, key],
    }));
  }

  function submit(event: React.FormEvent) {
    event.preventDefault();
    const found = validateProtocolForm(form);
    setErrors(found);
    if (Object.keys(found).length > 0) return;
    onSubmit(buildCreatePayload(form));
  }

  return (
    <div className="modalOverlay" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <form
        className="modalCard ewlDialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="ewl-add-title"
        data-testid="add-protocol-dialog"
        onSubmit={submit}
        noValidate
      >
        <div className="ewlDialogHeader">
          <div>
            <p className="sectionEyebrow">External Watchlist</p>
            <h2 id="ewl-add-title" className="ewlDialogTitle">Monitor Public Protocol</h2>
          </div>
          <button type="button" className="btn btn-ghost" onClick={onClose} aria-label="Close">Close</button>
        </div>

        <p className="ewlReadOnlyNote">{READ_ONLY_NOTE}</p>

        <div className="ewlFormGrid">
          <div className="formField">
            <label htmlFor="ewl-name">Protocol name <span className="requiredMark">*</span></label>
            <input id="ewl-name" ref={nameRef} value={form.name} maxLength={120} aria-invalid={Boolean(errors.name)}
              onChange={(event) => update('name', event.target.value)} placeholder="e.g. Spiko" />
            {errors.name ? <p className="fieldError">{errors.name}</p> : null}
          </div>
          <div className="formField">
            <label htmlFor="ewl-website">Website</label>
            <input id="ewl-website" value={form.website_url} maxLength={300} aria-invalid={Boolean(errors.website_url)}
              onChange={(event) => update('website_url', event.target.value)} placeholder="https://" />
            {errors.website_url ? <p className="fieldError">{errors.website_url}</p> : null}
          </div>
          <div className="formField">
            <label htmlFor="ewl-network">Network <span className="requiredMark">*</span></label>
            <select id="ewl-network" value={form.network} aria-invalid={Boolean(errors.network)}
              onChange={(event) => update('network', event.target.value)}>
              <option value="">Select a network</option>
              {networks.map((network) => (
                <option key={network.key} value={network.key} disabled={!network.rpc_configured}>
                  {network.label}{network.rpc_configured ? '' : ' — no RPC endpoint configured'}
                </option>
              ))}
            </select>
            {errors.network ? <p className="fieldError">{errors.network}</p> : null}
            <p className="inputHint">Only networks Decoda already monitors. A network without an RPC endpoint cannot be monitored.</p>
          </div>
          <div className="formField">
            <label htmlFor="ewl-target-type">Target type</label>
            <select id="ewl-target-type" value={form.target_type} onChange={(event) => update('target_type', event.target.value)}>
              {TARGET_TYPE_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>{option.label}</option>
              ))}
            </select>
          </div>
          <div className="formField ewlSpan2">
            <label htmlFor="ewl-address">Contract address</label>
            <input id="ewl-address" className="ewlMono" value={form.address} spellCheck={false} autoComplete="off"
              aria-invalid={Boolean(errors.address)} onChange={(event) => update('address', event.target.value)}
              placeholder="0x…" />
            {errors.address ? <p className="fieldError">{errors.address}</p> : null}
            <p className="inputHint">A public address. You can add more contracts, wallets, multisigs or oracles later.</p>
          </div>
          <div className="formField">
            <label htmlFor="ewl-label">Label</label>
            <input id="ewl-label" value={form.label} maxLength={120} onChange={(event) => update('label', event.target.value)}
              placeholder="e.g. EUTBL token" />
          </div>
          <div className="formField">
            <label htmlFor="ewl-backfill">Historical backfill</label>
            <select id="ewl-backfill" value={String(form.backfill_days)}
              onChange={(event) => update('backfill_days', Number(event.target.value))}>
              {BACKFILL_OPTIONS.map((option) => (
                <option key={option.value} value={String(option.value)}>{option.label}</option>
              ))}
            </select>
          </div>
        </div>

        <fieldset className="ewlProfiles">
          <legend>Detection profiles</legend>
          <div className="ewlProfileGrid">
            {profileOptions.map((profile) => (
              <label key={profile.key} className="ewlCheck">
                <input type="checkbox" checked={form.detection_profiles.includes(profile.key)}
                  onChange={() => toggleProfile(profile.key)} />
                <span>{profile.label}</span>
              </label>
            ))}
          </div>
          {errors.detection_profiles ? <p className="fieldError">{errors.detection_profiles}</p> : null}
        </fieldset>

        {serverError ? <p className="ewlFormError" role="alert">{serverError}</p> : null}

        <div className="ewlDialogActions">
          <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn btn-primary" disabled={!canSubmit || submitting} data-loading={submitting ? 'true' : undefined}>
            Start Monitoring
          </button>
        </div>
      </form>
    </div>
  );
}
