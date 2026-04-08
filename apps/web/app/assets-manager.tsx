'use client';

import { FormEvent, useEffect, useMemo, useRef, useState } from 'react';

import { usePilotAuth } from './pilot-auth-context';
import { parseTagInput } from './policy-builders';
import { classifyApiTransportError } from './auth-diagnostics';

type Props = { apiUrl: string };
type Asset = any;
type AssetForm = typeof EMPTY_ASSET;
type FieldName = 'name' | 'asset_type' | 'chain_network' | 'identifier';
type FieldErrors = Partial<Record<FieldName, string>>;

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
  const [form, setForm] = useState<AssetForm>(EMPTY_ASSET);
  const [search, setSearch] = useState('');
  const [filterType, setFilterType] = useState('all');
  const [submitError, setSubmitError] = useState('');
  const [successMessage, setSuccessMessage] = useState('');
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [touched, setTouched] = useState<Partial<Record<FieldName, boolean>>>({});
  const [submitting, setSubmitting] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const fieldRefs = useRef<Record<FieldName, HTMLInputElement | HTMLSelectElement | null>>({
    name: null,
    asset_type: null,
    chain_network: null,
    identifier: null,
  });

  async function load() {
    const response = await fetch(`${apiUrl}/assets`, { headers: { ...authHeaders() }, cache: 'no-store' });
    if (!response.ok) return;
    const payload = await response.json();
    setAssets(payload.assets ?? []);
  }

  function validate(values: AssetForm): FieldErrors {
    const next: FieldErrors = {};
    if (!values.name.trim()) next.name = 'Asset name is required.';
    if (!values.asset_type.trim()) next.asset_type = 'Asset type is required.';
    if (!values.chain_network.trim()) next.chain_network = 'Chain / network is required.';
    if (!values.identifier.trim()) next.identifier = 'Wallet address / identifier is required.';
    if (values.identifier.startsWith('0x') && !ADDRESS_REGEX.test(values.identifier)) {
      next.identifier = 'Enter a valid Ethereum address.';
    }
    return next;
  }

  function focusFirstInvalid(errors: FieldErrors) {
    const order: FieldName[] = ['name', 'asset_type', 'chain_network', 'identifier'];
    for (const field of order) {
      if (!errors[field]) continue;
      const element = fieldRefs.current[field];
      if (!element) continue;
      element.focus();
      element.scrollIntoView({ behavior: 'smooth', block: 'center' });
      break;
    }
  }

  const validationErrors = useMemo(() => validate(form), [form]);
  const isFormValid = Object.keys(validationErrors).length === 0;
  const blockedReason = !form.name.trim()
    ? 'Add an asset name to enable creation.'
    : !form.identifier.trim()
      ? 'Add a wallet address / identifier to enable creation.'
      : validationErrors.identifier
        ? validationErrors.identifier
        : '';

  async function createAsset(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSuccessMessage('');
    setSubmitError('');
    const errors = validate(form);
    setFieldErrors(errors);
    if (Object.keys(errors).length > 0) {
      setTouched({ name: true, asset_type: true, chain_network: true, identifier: true });
      focusFirstInvalid(errors);
      return;
    }
    setSubmitting(true);
    try {
      const response = await fetch(`${apiUrl}/assets`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ ...form, tags: form.tags }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        const detail = payload?.detail;
        const responseFieldErrors = (typeof detail === 'object' && detail?.field_errors && typeof detail.field_errors === 'object') ? detail.field_errors : null;
        if (responseFieldErrors) {
          setFieldErrors((prev) => ({ ...prev, ...responseFieldErrors }));
          setTouched({ name: true, asset_type: true, chain_network: true, identifier: true });
          focusFirstInvalid(responseFieldErrors as FieldErrors);
        }
        if (response.status >= 500) {
          setSubmitError('We could not create the asset due to a server issue. Please retry in a moment.');
          // eslint-disable-next-line no-console
          console.error('Asset create failed', { status: response.status, payload });
        } else {
          setSubmitError((typeof detail === 'object' && detail?.message) || detail || payload?.message || 'Unable to create asset. Review required fields and try again.');
        }
        return;
      }
      setForm(EMPTY_ASSET);
      setTouched({});
      setFieldErrors({});
      setShowAdvanced(false);
      setSuccessMessage(`Asset created successfully. Verification status: ${payload.verification_status ?? 'pending'}.`);
      await load();
    } catch (error) {
      setSubmitError(classifyApiTransportError('create this asset', apiUrl, error));
      // eslint-disable-next-line no-console
      console.error('Asset create request failed', error);
    } finally {
      setSubmitting(false);
    }
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
          {QUICK_PRESETS.map((preset) => <button key={preset.label} type="button" className="secondaryCta" onClick={() => {
            const shouldFocusName = !form.name.trim();
            setForm((current) => ({ ...current, ...preset.form, identifier: current.identifier || '0x5f6f35FD8b10C5576089f99C7c8c351Deb851d1F' }));
            setTouched((current) => ({ ...current, name: true, identifier: true }));
            setSuccessMessage('');
            setSubmitError('');
            if (shouldFocusName) requestAnimationFrame(() => fieldRefs.current.name?.focus());
          }}>{preset.label}</button>)}
        </div>
        <div className="buttonRow">
          <input aria-label="Search assets" placeholder="Search assets by name, chain, identifier, or owner" value={search} onChange={(event) => setSearch(event.target.value)} />
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
        <form onSubmit={(event) => void createAsset(event)} noValidate>
          <div className="formField">
            <label htmlFor="asset-name">Asset name <span aria-hidden="true" className="requiredMark">*</span></label>
            <input id="asset-name" ref={(node) => { fieldRefs.current.name = node; }} required aria-invalid={Boolean(touched.name && (fieldErrors.name || validationErrors.name))} aria-describedby="asset-name-help asset-name-error" placeholder="Friendly name (e.g., Treasury wallet)" value={form.name} onBlur={() => setTouched((current) => ({ ...current, name: true }))} onChange={(event) => { setForm({ ...form, name: event.target.value }); setFieldErrors((current) => ({ ...current, name: undefined })); setSubmitError(''); setSuccessMessage(''); }} />
            <p id="asset-name-help" className="inputHint">Use a clear name your team will recognize quickly.</p>
            {touched.name && (fieldErrors.name || validationErrors.name) ? <p id="asset-name-error" className="fieldError" role="alert">{fieldErrors.name || validationErrors.name}</p> : null}
          </div>
          <div className="buttonRow">
            <div className="formField">
              <label htmlFor="asset-type">Asset type <span aria-hidden="true" className="requiredMark">*</span></label>
              <select id="asset-type" ref={(node) => { fieldRefs.current.asset_type = node; }} required aria-invalid={Boolean(touched.asset_type && (fieldErrors.asset_type || validationErrors.asset_type))} aria-describedby="asset-type-error" value={form.asset_type} onBlur={() => setTouched((current) => ({ ...current, asset_type: true }))} onChange={(event) => { setForm({ ...form, asset_type: event.target.value }); setFieldErrors((current) => ({ ...current, asset_type: undefined })); }}>
                <option value="wallet">Wallet</option><option value="contract">Contract</option><option value="oracle">Oracle</option><option value="treasury-linked asset">Treasury-linked asset</option>
              </select>
              {touched.asset_type && (fieldErrors.asset_type || validationErrors.asset_type) ? <p id="asset-type-error" className="fieldError" role="alert">{fieldErrors.asset_type || validationErrors.asset_type}</p> : null}
            </div>
            <div className="formField">
              <label htmlFor="asset-chain">Chain / network <span aria-hidden="true" className="requiredMark">*</span></label>
              <input id="asset-chain" ref={(node) => { fieldRefs.current.chain_network = node; }} required aria-invalid={Boolean(touched.chain_network && (fieldErrors.chain_network || validationErrors.chain_network))} aria-describedby="asset-chain-error" placeholder="Chain / network (e.g., ethereum-mainnet)" value={form.chain_network} onBlur={() => setTouched((current) => ({ ...current, chain_network: true }))} onChange={(event) => { setForm({ ...form, chain_network: event.target.value }); setFieldErrors((current) => ({ ...current, chain_network: undefined })); }} />
              {touched.chain_network && (fieldErrors.chain_network || validationErrors.chain_network) ? <p id="asset-chain-error" className="fieldError" role="alert">{fieldErrors.chain_network || validationErrors.chain_network}</p> : null}
            </div>
          </div>
          <div className="formField">
            <label htmlFor="asset-identifier">Wallet address / identifier <span aria-hidden="true" className="requiredMark">*</span></label>
            <input id="asset-identifier" ref={(node) => { fieldRefs.current.identifier = node; }} required aria-invalid={Boolean(touched.identifier && (fieldErrors.identifier || validationErrors.identifier))} aria-describedby="asset-identifier-help asset-identifier-error" placeholder="Wallet or contract address / identifier" value={form.identifier} onBlur={() => setTouched((current) => ({ ...current, identifier: true }))} onChange={(event) => { setForm({ ...form, identifier: event.target.value.trim() }); setFieldErrors((current) => ({ ...current, identifier: undefined })); setSubmitError(''); setSuccessMessage(''); }} />
            <p id="asset-identifier-help" className="inputHint">Example wallet: 0x5f6f35FD8b10C5576089f99C7c8c351Deb851d1F</p>
            {touched.identifier && (fieldErrors.identifier || validationErrors.identifier) ? <p id="asset-identifier-error" className="fieldError" role="alert">{fieldErrors.identifier || validationErrors.identifier}</p> : null}
          </div>
          <div className="formField">
            <label htmlFor="asset-owner">Owner team</label>
            <input id="asset-owner" placeholder="Owner team (e.g., Treasury Ops)" value={form.owner_team} onChange={(event) => setForm({ ...form, owner_team: event.target.value })} />
          </div>
          <button type="button" className="secondaryCta" onClick={() => setShowAdvanced((value) => !value)}>{showAdvanced ? 'Hide advanced settings' : 'Advanced settings'}</button>
          {showAdvanced ? <><div className="formField"><label htmlFor="asset-tags">Tags</label><input id="asset-tags" placeholder="Tags (comma-separated)" value={form.tags.join(', ')} onChange={(event) => setForm({ ...form, tags: parseTagInput(event.target.value) })} /></div><div className="formField"><label htmlFor="asset-notes">Notes</label><textarea id="asset-notes" rows={3} placeholder="Optional metadata / notes" value={form.notes} onChange={(event) => setForm({ ...form, notes: event.target.value })} /></div></> : null}
          <div className="buttonRow">
            <button type="submit" disabled={!isFormValid || submitting}>{submitting ? 'Creating asset…' : 'Create asset'}</button>
          </div>
          {!isFormValid && !submitting ? <p className="inputHint" role="status">Create asset is disabled until required fields are valid. {blockedReason}</p> : null}
          {submitError ? <p className="statusLine" role="alert">{submitError}</p> : null}
          {successMessage ? <p className="statusLine successLine" role="status">{successMessage}</p> : null}
        </form>
      </section>
    </div>
  );
}
