'use client';

import { FormEvent, useEffect, useMemo, useRef, useState } from 'react';

import { usePilotAuth } from './pilot-auth-context';
import { parseTagInput } from './policy-builders';
import { classifyApiTransportError } from './auth-diagnostics';
import { normalizeApiBaseUrl, isValidApiBaseUrl } from './api-config';
import { DataTable, StatusPill, type PillVariant } from './components/ui-primitives';

type Props = { apiUrl: string };
type Asset = any;
type AssetForm = typeof EMPTY_ASSET;
type FieldName = 'name' | 'asset_type' | 'chain_network' | 'identifier';
type FieldErrors = Partial<Record<FieldName, string>>;

const EMPTY_ASSET = {
  name: '', asset_type: 'wallet', chain_network: 'ethereum-mainnet', identifier: '',
  owner_team: '', value_usd: '', tags: [] as string[], description: '', notes: '', enabled: true,
};

const QUICK_PRESETS = [
  { label: 'Ethereum Wallet', form: { name: 'Treasury wallet', asset_type: 'wallet', chain_network: 'ethereum-mainnet' } },
  { label: 'Smart Contract', form: { name: 'Core protocol contract', asset_type: 'smart-contract', chain_network: 'ethereum-mainnet' } },
  { label: 'Treasury Vault', form: { name: 'Treasury vault', asset_type: 'treasury-vault', chain_network: 'ethereum-mainnet' } },
] as const;

const ADDRESS_REGEX = /^0x[a-fA-F0-9]{40}$/;

function assetTypeLabel(type: string): string {
  switch (type?.toLowerCase()) {
    case 'wallet': return 'Wallet';
    case 'contract':
    case 'smart-contract': return 'Smart Contract';
    case 'treasury-linked asset':
    case 'treasury-vault': return 'Treasury Vault';
    case 'tokenized-rwa': return 'Tokenized RWA';
    case 'stablecoin': return 'Stablecoin / Cash';
    default: return type || 'Other';
  }
}

function assetStatusInfo(asset: Asset): { label: string; variant: PillVariant } {
  const vs = asset?.verification_status?.toLowerCase();
  switch (vs) {
    case 'verified':
    case 'active': return { label: 'Active', variant: 'success' };
    case 'pending': return { label: 'Pending Verification', variant: 'warning' };
    case 'failed':
    case 'verification_failed': return { label: 'Verification Failed', variant: 'danger' };
    case 'archived': return { label: 'Archived', variant: 'neutral' };
    default: return { label: 'Unknown', variant: 'neutral' };
  }
}

/**
 * Returns monitoring column status.
 * Never returns "Monitoring" unless a valid monitored_system exists and reports telemetry.
 */
export function getMonitoringStatus(asset: Asset): { label: string; variant: PillVariant } {
  const status = asset?.monitoring_link_status;
  const hasLinkedSystem = asset?.has_linked_monitored_system !== false;
  const systemCount = asset?.monitoring_systems_count ?? (status === 'attached' ? 1 : -1);

  if (!status || status === 'not_configured' || status === 'target_missing') {
    return { label: 'Target missing', variant: 'warning' };
  }
  if (status === 'system_missing' || !hasLinkedSystem || systemCount === 0) {
    return { label: 'System not enabled', variant: 'warning' };
  }
  if (asset?.has_heartbeat === false) {
    return { label: 'Not reporting', variant: 'warning' };
  }
  if (asset?.has_telemetry === false) {
    return { label: 'Waiting for telemetry', variant: 'neutral' };
  }
  if (asset?.telemetry_fresh === false) {
    return { label: 'Telemetry stale', variant: 'warning' };
  }
  if (status === 'attached') {
    return { label: 'Monitoring', variant: 'success' };
  }
  return { label: 'Target missing', variant: 'warning' };
}

/** Exported for backwards-compat and direct test use. */
export function monitoringLinkStatusLabel(asset: Asset): string {
  return getMonitoringStatus(asset).label;
}

function assetNextAction(asset: Asset): string {
  const vs = asset?.verification_status?.toLowerCase();
  if (!vs || vs === 'unknown' || vs === 'pending' || vs === 'failed') return 'Verify asset';

  const monStatus = asset?.monitoring_link_status;
  if (!monStatus || monStatus === 'not_configured' || monStatus === 'target_missing') {
    return 'Create monitoring target';
  }
  if (monStatus === 'system_missing' || asset?.has_linked_monitored_system === false) {
    return 'Enable monitored system';
  }
  if (asset?.has_heartbeat === false) return 'Start simulator signal';
  if (asset?.has_telemetry === false) return 'Wait for telemetry';
  if ((asset?.open_incidents ?? 0) > 0) return 'Open incident';
  if ((asset?.active_alerts ?? 0) > 0) return 'View alerts';
  return 'View detections';
}

function assetMatchesTypeFilter(asset: Asset, filterValue: string): boolean {
  if (filterValue === 'all') return true;
  const type = asset?.asset_type?.toLowerCase() || '';
  switch (filterValue) {
    case 'wallet': return type === 'wallet';
    case 'smart-contract': return type === 'smart-contract' || type === 'contract';
    case 'treasury-vault': return type === 'treasury-vault' || type === 'treasury-linked asset';
    case 'tokenized-rwa': return type === 'tokenized-rwa';
    case 'stablecoin': return type === 'stablecoin';
    case 'other': return !['wallet', 'smart-contract', 'contract', 'treasury-vault', 'treasury-linked asset', 'tokenized-rwa', 'stablecoin'].includes(type);
    default: return false;
  }
}

export default function AssetsManager({ apiUrl }: Props) {
  const { authHeaders, signOut } = usePilotAuth();
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
  const formRef = useRef<HTMLElement | null>(null);
  const fieldRefs = useRef<Record<FieldName, HTMLInputElement | HTMLSelectElement | null>>({
    name: null, asset_type: null, chain_network: null, identifier: null,
  });

  async function load() {
    const normalizedApiUrl = normalizeApiBaseUrl(apiUrl);
    if (!normalizedApiUrl || !isValidApiBaseUrl(normalizedApiUrl)) {
      setSubmitError('Cannot load assets because NEXT_PUBLIC_API_URL / API_URL is missing or invalid for this deployment.');
      return;
    }
    const headers = authHeaders();
    if (!headers.Authorization) {
      setSubmitError('Your session is missing or expired. Please sign in again.');
      return;
    }
    const response = await fetch(`${apiUrl}/assets`, { headers: { ...headers }, cache: 'no-store' });
    if (response.status === 401 || response.status === 403) {
      await signOut();
      setSubmitError('Your session is missing or expired. Please sign in again.');
      return;
    }
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
      const normalizedApiUrl = normalizeApiBaseUrl(apiUrl);
      if (!normalizedApiUrl || !isValidApiBaseUrl(normalizedApiUrl)) {
        setSubmitError('Cannot create this asset because NEXT_PUBLIC_API_URL / API_URL is missing or invalid for this deployment.');
        return;
      }
      const headers = authHeaders();
      if (!headers.Authorization) {
        setSubmitError('Your session is missing or expired. Please sign in again.');
        return;
      }
      const response = await fetch(`${apiUrl}/assets`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...headers },
        body: JSON.stringify({ ...form, tags: form.tags }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        if (response.status === 401 || response.status === 403) {
          await signOut();
          setSubmitError('Your session is missing or expired. Please sign in again.');
          return;
        }
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
    .filter((asset) => assetMatchesTypeFilter(asset, filterType))
    .filter((asset) => `${asset.name} ${asset.identifier} ${asset.chain_network} ${asset.owner_team || ''}`.toLowerCase().includes(search.toLowerCase())),
  [assets, search, filterType]);

  useEffect(() => { void load(); }, []);

  return (
    <>
      {/* ── Page header ──────────────────────────────────────────── */}
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: '1.5rem', gap: '1rem', flexWrap: 'wrap' }}>
        <div>
          <h1 style={{ margin: 0, fontSize: '1.5rem', fontWeight: 700, color: 'var(--text-primary)' }}>Protected Assets</h1>
          <p style={{ margin: '0.3rem 0 0', color: 'var(--text-secondary)', fontSize: '0.9rem' }}>
            Manage your protected real-world assets.
          </p>
        </div>
        <button
          type="button"
          className="btn btn-primary"
          onClick={() => { formRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }); }}
        >
          Add Asset
        </button>
      </div>

      {/* ── Filters ──────────────────────────────────────────────── */}
      <div style={{ display: 'flex', gap: '0.75rem', marginBottom: '1.25rem', flexWrap: 'wrap' }}>
        <input
          aria-label="Search assets"
          placeholder="Search assets..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          style={{ flex: '1 1 240px', minWidth: '160px' }}
        />
        <select
          value={filterType}
          onChange={(e) => setFilterType(e.target.value)}
          aria-label="Filter by asset type"
          style={{ flex: '0 0 auto', minWidth: '150px' }}
        >
          <option value="all">All Types</option>
          <option value="wallet">Wallet</option>
          <option value="smart-contract">Smart Contract</option>
          <option value="treasury-vault">Treasury Vault</option>
          <option value="tokenized-rwa">Tokenized RWA</option>
          <option value="stablecoin">Stablecoin / Cash</option>
          <option value="other">Other</option>
        </select>
      </div>

      {/* ── Registry table ───────────────────────────────────────── */}
      {filtered.length === 0 ? (
        <div
          className="emptyStatePanel"
          style={{ textAlign: 'center', padding: '4rem 2rem', margin: '0 0 2rem' }}
        >
          <p style={{ fontSize: '2.5rem', margin: '0 0 1rem', lineHeight: 1 }}>🛡</p>
          <h3 style={{ margin: '0 0 0.5rem', fontSize: '1.1rem', color: 'var(--text-primary)' }}>
            No protected assets yet
          </h3>
          <p className="muted" style={{ margin: '0 0 1.5rem', maxWidth: '44ch', marginInline: 'auto' }}>
            Add your first wallet, smart contract, treasury vault, or tokenized RWA to begin monitoring.
          </p>
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => { formRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }); }}
          >
            Add Asset
          </button>
        </div>
      ) : (
        <DataTable
          headers={['Name', 'Type', 'Network', 'Value / Exposure', 'Status', 'Monitoring', 'Next Action']}
          compact
        >
          {filtered.map((asset) => {
            const statusInfo = assetStatusInfo(asset);
            const monInfo = getMonitoringStatus(asset);
            const action = assetNextAction(asset);
            return (
              <tr key={asset.id}>
                <td>
                  <strong style={{ display: 'block', color: 'var(--text-primary)' }}>{asset.name}</strong>
                  {asset.identifier ? (
                    <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)', fontFamily: 'monospace' }}>
                      {asset.identifier}
                    </span>
                  ) : null}
                </td>
                <td>{assetTypeLabel(asset.asset_type)}</td>
                <td>{asset.chain_network || '--'}</td>
                <td>
                  {asset.value_usd
                    ? `$${Number(asset.value_usd).toLocaleString()}`
                    : '--'}
                </td>
                <td><StatusPill label={statusInfo.label} variant={statusInfo.variant} /></td>
                <td><StatusPill label={monInfo.label} variant={monInfo.variant} /></td>
                <td>
                  <span style={{ fontSize: '0.8rem', color: 'var(--text-accent)' }}>{action}</span>
                </td>
              </tr>
            );
          })}
        </DataTable>
      )}

      {/* ── Add asset form ───────────────────────────────────────── */}
      <section ref={formRef} id="asset-create-form" className="dataCard" style={{ marginTop: '2.5rem' }}>
        <p className="sectionEyebrow">Add asset</p>
        <h2>Register a new protected asset</h2>
        <div className="chipRow" style={{ marginBottom: '1rem' }}>
          {QUICK_PRESETS.map((preset) => (
            <button
              key={preset.label}
              type="button"
              className="secondaryCta"
              onClick={() => {
                const shouldFocusName = !form.name.trim();
                setForm((current) => ({ ...current, ...preset.form, identifier: current.identifier || '0x5f6f35FD8b10C5576089f99C7c8c351Deb851d1F' }));
                setTouched((current) => ({ ...current, name: true, identifier: true }));
                setSuccessMessage('');
                setSubmitError('');
                if (shouldFocusName) requestAnimationFrame(() => fieldRefs.current.name?.focus());
              }}
            >
              {preset.label}
            </button>
          ))}
        </div>
        <form onSubmit={(e) => void createAsset(e)} noValidate>
          <div className="formField">
            <label htmlFor="asset-name">Asset name <span aria-hidden="true" className="requiredMark">*</span></label>
            <input
              id="asset-name"
              ref={(node) => { fieldRefs.current.name = node; }}
              required
              aria-invalid={Boolean(touched.name && (fieldErrors.name || validationErrors.name))}
              aria-describedby="asset-name-help asset-name-error"
              placeholder="Friendly name (e.g., Treasury wallet)"
              value={form.name}
              onBlur={() => setTouched((c) => ({ ...c, name: true }))}
              onChange={(e) => { setForm({ ...form, name: e.target.value }); setFieldErrors((c) => ({ ...c, name: undefined })); setSubmitError(''); setSuccessMessage(''); }}
            />
            <p id="asset-name-help" className="inputHint">Use a clear name your team will recognize quickly.</p>
            {touched.name && (fieldErrors.name || validationErrors.name)
              ? <p id="asset-name-error" className="fieldError" role="alert">{fieldErrors.name || validationErrors.name}</p>
              : null}
          </div>
          <div className="buttonRow">
            <div className="formField">
              <label htmlFor="asset-type">Asset type <span aria-hidden="true" className="requiredMark">*</span></label>
              <select
                id="asset-type"
                ref={(node) => { fieldRefs.current.asset_type = node; }}
                required
                aria-invalid={Boolean(touched.asset_type && (fieldErrors.asset_type || validationErrors.asset_type))}
                aria-describedby="asset-type-error"
                value={form.asset_type}
                onBlur={() => setTouched((c) => ({ ...c, asset_type: true }))}
                onChange={(e) => { setForm({ ...form, asset_type: e.target.value }); setFieldErrors((c) => ({ ...c, asset_type: undefined })); }}
              >
                <option value="wallet">Wallet</option>
                <option value="smart-contract">Smart Contract</option>
                <option value="treasury-vault">Treasury Vault</option>
                <option value="tokenized-rwa">Tokenized RWA</option>
                <option value="stablecoin">Stablecoin / Cash</option>
                <option value="other">Other</option>
              </select>
              {touched.asset_type && (fieldErrors.asset_type || validationErrors.asset_type)
                ? <p id="asset-type-error" className="fieldError" role="alert">{fieldErrors.asset_type || validationErrors.asset_type}</p>
                : null}
            </div>
            <div className="formField">
              <label htmlFor="asset-chain">Chain / network <span aria-hidden="true" className="requiredMark">*</span></label>
              <input
                id="asset-chain"
                ref={(node) => { fieldRefs.current.chain_network = node; }}
                required
                aria-invalid={Boolean(touched.chain_network && (fieldErrors.chain_network || validationErrors.chain_network))}
                aria-describedby="asset-chain-error"
                placeholder="Chain / network (e.g., ethereum-mainnet)"
                value={form.chain_network}
                onBlur={() => setTouched((c) => ({ ...c, chain_network: true }))}
                onChange={(e) => { setForm({ ...form, chain_network: e.target.value }); setFieldErrors((c) => ({ ...c, chain_network: undefined })); }}
              />
              {touched.chain_network && (fieldErrors.chain_network || validationErrors.chain_network)
                ? <p id="asset-chain-error" className="fieldError" role="alert">{fieldErrors.chain_network || validationErrors.chain_network}</p>
                : null}
            </div>
          </div>
          <div className="formField">
            <label htmlFor="asset-identifier">Wallet address / identifier <span aria-hidden="true" className="requiredMark">*</span></label>
            <input
              id="asset-identifier"
              ref={(node) => { fieldRefs.current.identifier = node; }}
              required
              aria-invalid={Boolean(touched.identifier && (fieldErrors.identifier || validationErrors.identifier))}
              aria-describedby="asset-identifier-help asset-identifier-error"
              placeholder="Wallet or contract address / identifier"
              value={form.identifier}
              onBlur={() => setTouched((c) => ({ ...c, identifier: true }))}
              onChange={(e) => { setForm({ ...form, identifier: e.target.value.trim() }); setFieldErrors((c) => ({ ...c, identifier: undefined })); setSubmitError(''); setSuccessMessage(''); }}
            />
            <p id="asset-identifier-help" className="inputHint">Example wallet: 0x5f6f35FD8b10C5576089f99C7c8c351Deb851d1F</p>
            {touched.identifier && (fieldErrors.identifier || validationErrors.identifier)
              ? <p id="asset-identifier-error" className="fieldError" role="alert">{fieldErrors.identifier || validationErrors.identifier}</p>
              : null}
          </div>
          <div className="buttonRow">
            <div className="formField">
              <label htmlFor="asset-owner">Owner team</label>
              <input
                id="asset-owner"
                placeholder="Owner team (e.g., Treasury Ops)"
                value={form.owner_team}
                onChange={(e) => setForm({ ...form, owner_team: e.target.value })}
              />
            </div>
            <div className="formField">
              <label htmlFor="asset-value">Value / Exposure (USD)</label>
              <input
                id="asset-value"
                type="number"
                placeholder="e.g., 1000000"
                value={form.value_usd}
                onChange={(e) => setForm({ ...form, value_usd: e.target.value })}
              />
            </div>
          </div>
          <button type="button" className="secondaryCta" onClick={() => setShowAdvanced((v) => !v)}>
            {showAdvanced ? 'Hide advanced settings' : 'Advanced settings'}
          </button>
          {showAdvanced ? (
            <>
              <div className="formField">
                <label htmlFor="asset-tags">Tags</label>
                <input
                  id="asset-tags"
                  placeholder="Tags (comma-separated)"
                  value={form.tags.join(', ')}
                  onChange={(e) => setForm({ ...form, tags: parseTagInput(e.target.value) })}
                />
              </div>
              <div className="formField">
                <label htmlFor="asset-notes">Notes</label>
                <textarea
                  id="asset-notes"
                  rows={3}
                  placeholder="Optional metadata / notes"
                  value={form.notes}
                  onChange={(e) => setForm({ ...form, notes: e.target.value })}
                />
              </div>
            </>
          ) : null}
          <div className="buttonRow">
            <button type="submit" className="btn btn-primary" disabled={!isFormValid || submitting}>
              {submitting ? 'Creating asset…' : 'Create asset'}
            </button>
          </div>
          {!isFormValid && !submitting
            ? <p className="inputHint" role="status">Create asset is disabled until required fields are valid. {blockedReason}</p>
            : null}
          {submitError ? <p className="statusLine" role="alert">{submitError}</p> : null}
          {successMessage ? <p className="statusLine successLine" role="status">{successMessage}</p> : null}
        </form>
      </section>
    </>
  );
}
