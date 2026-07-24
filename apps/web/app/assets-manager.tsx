'use client';

import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import Link from 'next/link';

import { usePilotAuth } from './pilot-auth-context';
import { parseTagInput } from './policy-builders';
import { classifyApiTransportError } from './auth-diagnostics';
import { normalizeApiBaseUrl, isValidApiBaseUrl } from './api-config';
import { DataTable, StatusPill, type PillVariant } from './components/ui-primitives';
import AssetRiskAssessorPanel from './asset-risk-assessor-panel';
import {
  RISK_SCORE_TOOLTIP,
  RWA_TYPE_OPTIONS,
  assessmentStatusLabel,
  assessmentStatusVariant,
  isReserveBackedRwaType,
  formatPercent,
  formatUsd,
  monitoringHealthLabel,
  monitoringHealthVariant,
  relativeTime,
  reserveStatusLabel,
  reserveStatusVariant,
  riskLevelForScore,
  riskLevelLabel,
  riskLevelVariant,
  rwaTypeLabel,
  type RiskLevel,
} from './asset-risk-presentation';

type Props = { apiUrl: string };
type Asset = any;
type AssetForm = typeof EMPTY_ASSET;
type FieldName = 'name' | 'asset_type' | 'chain_network' | 'identifier';
type FieldErrors = Partial<Record<FieldName, string>>;

type Filters = {
  search: string;
  asset_type: string;
  network: string;
  risk_level: string;
  monitoring_health: string;
  custodian: string;
  sort: string;
  dir: string;
  page: number;
};

const DEFAULT_FILTERS: Filters = {
  search: '', asset_type: 'all', network: 'all', risk_level: 'all', monitoring_health: 'all',
  custodian: 'all', sort: 'risk', dir: 'desc', page: 1,
};
const PAGE_SIZE = 25;

// Technical monitoring taxonomy value (backend ASSET_TYPES). RWA product type is
// captured separately in rwa_asset_type. Default 'contract' — RWA tokens are
// on-chain contracts and 'contract' is a valid backend asset_type.
const EMPTY_ASSET = {
  name: '', asset_type: 'contract', rwa_asset_type: 'tokenized_treasury', chain_network: 'ethereum-mainnet', identifier: '',
  custodian: '', token_symbol: '', token_contract_address: '', token_decimals: '', owner_team: '', value_usd: '', tags: [] as string[], description: '', notes: '', enabled: true,
  price_source: '', reference_price_usd: '', circulating_supply: '',
  reserve_feed_type: 'none', reserve_feed_identifier: '', reserve_value_usd: '', reserve_verified: false, reserve_update_interval_seconds: '',
};

const QUICK_PRESETS = [
  { label: 'Ethereum Wallet', form: { name: 'Treasury wallet', asset_type: 'wallet', rwa_asset_type: 'other', chain_network: 'ethereum-mainnet' } },
  { label: 'Smart Contract', form: { name: 'Core protocol contract', asset_type: 'contract', rwa_asset_type: 'other', chain_network: 'ethereum-mainnet' } },
  { label: 'Treasury Vault', form: { name: 'Treasury vault', asset_type: 'treasury-linked asset', rwa_asset_type: 'tokenized_treasury', chain_network: 'ethereum-mainnet' } },
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
 * Uses backend-computed monitoring_status when available; falls back to field-level logic.
 * Never returns "Monitoring" unless a valid monitored_system exists and reports telemetry.
 */
export function getMonitoringStatus(asset: Asset): { label: string; variant: PillVariant } {
  // Backend-computed monitoring_status takes precedence when present
  const backendStatus = asset?.monitoring_status;
  if (backendStatus === 'live_verified') {
    return { label: 'Live telemetry verified', variant: 'success' };
  }
  if (backendStatus === 'not_linked') {
    return { label: 'Telemetry unlinked', variant: 'warning' };
  }
  if (backendStatus === 'not_configured') {
    return { label: 'Not configured', variant: 'neutral' };
  }
  if (backendStatus === 'error') {
    return { label: 'Provider issue', variant: 'danger' };
  }

  // Fall back to field-level logic (backward compat / when backend_status absent)
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
  // fail-closed: only show "Monitoring" when telemetry is explicitly confirmed
  if (asset?.has_telemetry !== true) {
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

  // Use backend-computed label when present (covers not_linked, live_verified, etc.)
  if (asset?.next_action_label) return asset.next_action_label;

  const monStatus = asset?.monitoring_link_status;
  if (!monStatus || monStatus === 'not_configured' || monStatus === 'target_missing') {
    return 'Connect provider';
  }
  if (monStatus === 'system_missing' || asset?.has_linked_monitored_system === false) {
    return 'Start worker';
  }
  if (asset?.has_heartbeat === false) return 'Start worker';
  if (asset?.has_telemetry !== true) return 'Verify telemetry';
  if ((asset?.open_incidents ?? 0) > 0) return 'Open incident';
  if ((asset?.active_alerts ?? 0) > 0) return 'View alerts';
  if (asset?.last_detection_at) return 'View detections';
  return 'View detections';
}

/** Human label for a reserve feed type (config value → display). */
function reserveFeedTypeLabel(type: string | null | undefined): string {
  switch ((type || '').toLowerCase()) {
    case 'manual': return 'Manual attestation';
    case 'attestation': return 'Attestation report';
    case 'proof_of_reserve': return 'Proof of reserve';
    case 'api': return 'Reserve API';
    case 'none':
    case '': return 'Not configured';
    default: return type as string;
  }
}

/** Truthful monitoring-health tooltip so an unavailable value is explained. */
function monitoringHealthTooltip(health: string): string {
  switch ((health || '').toLowerCase()) {
    case 'healthy': return 'Monitoring is live and telemetry is fresh.';
    case 'warning': return 'Monitoring is degraded — telemetry is missing or stale.';
    case 'critical': return 'Monitoring coverage is critically incomplete.';
    case 'degraded': return 'A provider failed or evidence is stale; showing last known state.';
    case 'provisioning': return 'Monitoring is being provisioned.';
    case 'not_configured': return 'No monitoring target is linked to this asset yet.';
    default: return 'Monitoring state could not be classified from backend facts.';
  }
}

/** — cell with an explanatory tooltip for a genuinely-absent value. */
function AbsentCell({ reason }: { reason: string }) {
  return <span className="muted" title={reason} aria-label={reason}>—</span>;
}

/** Provenance label for a value in the details drawer. */
type DataLabelKind = 'live' | 'delayed' | 'estimated' | 'unverified' | 'missing' | 'not_applicable';
function DataLabel({ kind }: { kind: DataLabelKind }) {
  const map: Record<DataLabelKind, [string, PillVariant]> = {
    live: ['Live', 'success'],
    delayed: ['Delayed', 'warning'],
    estimated: ['Estimated', 'warning'],
    unverified: ['Unverified', 'warning'],
    missing: ['Missing', 'neutral'],
    not_applicable: ['Not applicable', 'info'],
  };
  const [label, variant] = map[kind];
  return <StatusPill label={label} variant={variant} />;
}

/* ── Assessment status cell (status + last assessed time) ─────────── */
function AssessmentCell({ asset }: { asset: Asset }) {
  const status = String(asset.assessment_status || (asset.risk_score == null ? 'not_assessed' : 'completed'));
  const lastAssessed = asset.last_assessed_at as string | null | undefined;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.15rem', alignItems: 'flex-start' }}>
      <StatusPill label={assessmentStatusLabel(status)} variant={assessmentStatusVariant(status)} />
      <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }} title={lastAssessed ? `Last assessed ${new Date(lastAssessed).toLocaleString()}` : 'This asset has not been assessed yet.'}>
        {lastAssessed ? relativeTime(lastAssessed) : 'never'}
      </span>
    </div>
  );
}

/* ── Risk badge (compact colored score with tooltip) ──────────────── */
function RiskBadge({ score, level }: { score: number | null | undefined; level?: string | null }) {
  const resolvedLevel: RiskLevel = (level as RiskLevel) || riskLevelForScore(score);
  if (score === null || score === undefined || resolvedLevel === 'unassessed') {
    return <span className="riskBadge riskBadge-unassessed" title={RISK_SCORE_TOOLTIP}>--</span>;
  }
  return (
    <span
      className={`riskBadge riskBadge-${resolvedLevel}`}
      title={RISK_SCORE_TOOLTIP}
      aria-label={`Risk score ${score} of 100 (${riskLevelLabel(resolvedLevel)})`}
    >
      {score}
    </span>
  );
}

export default function AssetsManager({ apiUrl }: Props) {
  const { authHeaders, signOut, refreshCsrfToken } = usePilotAuth();
  const [assets, setAssets] = useState<Asset[]>([]);
  const [pagination, setPagination] = useState<{ filtered_total: number; total: number; page: number; page_size: number }>({ filtered_total: 0, total: 0, page: 1, page_size: PAGE_SIZE });
  const [facets, setFacets] = useState<{ networks: string[]; custodians: string[] }>({ networks: [], custodians: [] });
  const [filters, setFilters] = useState<Filters>(DEFAULT_FILTERS);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [form, setForm] = useState<AssetForm>(EMPTY_ASSET);
  const [submitError, setSubmitError] = useState('');
  const [successMessage, setSuccessMessage] = useState('');
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [touched, setTouched] = useState<Partial<Record<FieldName, boolean>>>({});
  const [submitting, setSubmitting] = useState(false);
  const [actionLoadingAssetId, setActionLoadingAssetId] = useState<string | null>(null);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [showAddModal, setShowAddModal] = useState(false);
  const [drawerAsset, setDrawerAsset] = useState<Asset | null>(null);
  const [panelRefresh, setPanelRefresh] = useState(0);
  const [workspaceAssessing, setWorkspaceAssessing] = useState(false);
  const [assessmentProgress, setAssessmentProgress] = useState('');
  const fieldRefs = useRef<Record<FieldName, HTMLInputElement | HTMLSelectElement | null>>({
    name: null, asset_type: null, chain_network: null, identifier: null,
  });

  /* ── URL <-> filter state (preserve filters in the URL) ──────────── */
  useEffect(() => {
    if (typeof window === 'undefined') return;
    const params = new URLSearchParams(window.location.search);
    setFilters((current) => ({
      ...current,
      search: params.get('search') ?? current.search,
      asset_type: params.get('asset_type') ?? current.asset_type,
      network: params.get('network') ?? current.network,
      risk_level: params.get('risk_level') ?? current.risk_level,
      monitoring_health: params.get('monitoring_health') ?? current.monitoring_health,
      custodian: params.get('custodian') ?? current.custodian,
      sort: params.get('sort') ?? current.sort,
      dir: params.get('dir') ?? current.dir,
      page: Number(params.get('page') ?? current.page) || 1,
    }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const queryString = useMemo(() => {
    const params = new URLSearchParams();
    if (filters.search) params.set('search', filters.search);
    if (filters.asset_type !== 'all') params.set('asset_type', filters.asset_type);
    if (filters.network !== 'all') params.set('network', filters.network);
    if (filters.risk_level !== 'all') params.set('risk_level', filters.risk_level);
    if (filters.monitoring_health !== 'all') params.set('monitoring_health', filters.monitoring_health);
    if (filters.custodian !== 'all') params.set('custodian', filters.custodian);
    params.set('sort', filters.sort);
    params.set('dir', filters.dir);
    params.set('page', String(filters.page));
    params.set('page_size', String(PAGE_SIZE));
    return params.toString();
  }, [filters]);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    const next = `${window.location.pathname}?${queryString}`;
    window.history.replaceState(null, '', next);
  }, [queryString]);

  const load = useCallback(async () => {
    setLoadError('');
    const normalizedApiUrl = normalizeApiBaseUrl(apiUrl);
    if (!normalizedApiUrl || !isValidApiBaseUrl(normalizedApiUrl)) {
      setLoadError('Unable to load assets: API endpoint is not configured. Contact your administrator or check System Health.');
      setLoading(false);
      return;
    }
    const headers = authHeaders();
    if (!headers.Authorization) {
      setLoadError('Your session is missing or expired. Please sign in again.');
      setLoading(false);
      return;
    }
    try {
      const response = await fetch(`/api/assets?${queryString}`, { headers: { ...headers }, cache: 'no-store' });
      if (response.status === 401 || response.status === 403) {
        await signOut();
        setLoadError('Your session is missing or expired. Please sign in again.');
        return;
      }
      if (!response.ok) {
        setLoadError('We could not load protected assets right now. Please retry.');
        return;
      }
      const payload = await response.json();
      setAssets(payload.assets ?? []);
      if (payload.pagination) setPagination(payload.pagination);
      if (payload.facets) setFacets({ networks: payload.facets.networks ?? [], custodians: payload.facets.custodians ?? [] });
    } catch (error) {
      setLoadError(classifyApiTransportError('load protected assets', apiUrl, error));
    } finally {
      setLoading(false);
    }
  }, [apiUrl, authHeaders, queryString, signOut]);

  useEffect(() => { setLoading(true); void load(); }, [load]);

  function updateFilter(patch: Partial<Filters>) {
    setFilters((current) => ({ ...current, ...patch, page: patch.page ?? 1 }));
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
  // Progressive disclosure: reserve-backed RWA types (or an explicit feed) surface
  // the reserve configuration; wallets do not require it.
  const reserveBacked = isReserveBackedRwaType(form.rwa_asset_type) || form.reserve_feed_type !== 'none';
  const isWalletType = form.asset_type === 'wallet';
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
        setSubmitError('Unable to create asset: API endpoint is not configured. Contact your administrator or check System Health.');
        return;
      }
      const headers = authHeaders();
      if (!headers.Authorization) {
        setSubmitError('Your session is missing or expired. Please sign in again.');
        return;
      }
      const assetBody = JSON.stringify({ ...form, tags: form.tags });
      let response = await fetch('/api/assets', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...headers },
        body: assetBody,
      });
      // On CSRF expired/invalid, refresh the token once and retry automatically.
      if (response.status === 403) {
        const initialPayload = await response.json().catch(() => ({}));
        const isCsrfFailure = initialPayload?.code === 'CSRF_INVALID' || initialPayload?.code === 'csrf_invalid'
          || initialPayload?.code === 'CSRF_EXPIRED' || initialPayload?.code === 'csrf_expired';
        if (isCsrfFailure) {
          const freshToken = await refreshCsrfToken();
          if (freshToken) {
            const retryHeaders = { 'Content-Type': 'application/json', ...headers, 'X-CSRF-Token': freshToken };
            response = await fetch('/api/assets', { method: 'POST', headers: retryHeaders, body: assetBody });
            if (response.status === 403) {
              setSubmitError('Security token expired. We refreshed it. Try again.');
              return;
            }
          } else {
            setSubmitError('Security token expired. We refreshed it. Try again.');
            return;
          }
        }
      }
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        if (response.status === 401) {
          await signOut();
          setSubmitError('Your session is missing or expired. Please sign in again.');
          return;
        }
        if (response.status === 403) {
          if (payload?.code === 'CSRF_INVALID' || payload?.code === 'csrf_invalid') {
            setSubmitError('Security token expired. We refreshed it. Try again.');
            return;
          }
          await signOut();
          setSubmitError('Your session is missing or expired. Please sign in again.');
          return;
        }
        if (response.status === 404) {
          setSubmitError('Asset creation endpoint not found (HTTP 404). Contact your administrator or check System Health.');
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
      setShowAddModal(false);
      setSuccessMessage(`Asset created successfully. Verification status: ${payload.verification_status ?? 'pending'}.`);
      setPanelRefresh((v) => v + 1);
      await load();
    } catch (error) {
      setSubmitError(classifyApiTransportError('create this asset', apiUrl, error));
      // eslint-disable-next-line no-console
      console.error('Asset create request failed', error);
    } finally {
      setSubmitting(false);
    }
  }

  async function runNextAction(asset: Asset, action: string) {
    setSubmitError('');
    setSuccessMessage('');
    if (action === 'Verify asset') {
      setActionLoadingAssetId(asset.id);
      try {
        const response = await fetch(`${apiUrl}/assets/${asset.id}/verify`, { method: 'POST', headers: { ...authHeaders() } });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
          setSubmitError(payload?.detail || 'Unable to verify this asset right now.');
          return;
        }
        setSuccessMessage('Asset verified and monitoring prerequisites have been reconciled.');
        setPanelRefresh((v) => v + 1);
        await load();
      } catch (error) {
        setSubmitError(classifyApiTransportError('verify this asset', apiUrl, error));
      } finally {
        setActionLoadingAssetId(null);
      }
    }
  }

  async function runAssessment(asset: Asset): Promise<any | null> {
    setSubmitError('');
    setActionLoadingAssetId(asset.id);
    try {
      const headers = authHeaders();
      let response = await fetch(`/api/assets/${asset.id}/risk-assessment`, { method: 'POST', headers: { ...headers } });
      if (response.status === 403) {
        const initial = await response.json().catch(() => ({}));
        if (initial?.code === 'CSRF_INVALID' || initial?.code === 'csrf_invalid' || initial?.code === 'CSRF_EXPIRED') {
          const freshToken = await refreshCsrfToken();
          if (freshToken) {
            response = await fetch(`/api/assets/${asset.id}/risk-assessment`, { method: 'POST', headers: { ...headers, 'X-CSRF-Token': freshToken } });
          }
        }
      }
      const payload = await response.json().catch(() => ({}));
      if (response.status === 401) {
        await signOut();
        setSubmitError('Your session is missing or expired. Please sign in again.');
        return null;
      }
      if (!response.ok) {
        setSubmitError((typeof payload?.detail === 'string' && payload.detail) || 'Unable to run the assessment right now.');
        return null;
      }
      setPanelRefresh((v) => v + 1);
      await load();
      return payload;
    } catch (error) {
      setSubmitError(classifyApiTransportError('run this assessment', apiUrl, error));
      return null;
    } finally {
      setActionLoadingAssetId(null);
    }
  }

  // Fire a single idempotent assessment for one asset (no per-row spinner state).
  const assessOne = useCallback(async (assetId: string): Promise<boolean> => {
    const headers = authHeaders();
    let response = await fetch(`/api/assets/${assetId}/risk-assessment`, { method: 'POST', headers: { ...headers } });
    if (response.status === 403) {
      const initial = await response.json().catch(() => ({}));
      if (initial?.code === 'CSRF_INVALID' || initial?.code === 'csrf_invalid' || initial?.code === 'CSRF_EXPIRED') {
        const freshToken = await refreshCsrfToken();
        if (freshToken) {
          response = await fetch(`/api/assets/${assetId}/risk-assessment`, { method: 'POST', headers: { ...headers, 'X-CSRF-Token': freshToken } });
        }
      }
    }
    return response.ok || response.status === 409; // 409 => an active job already exists (idempotent)
  }, [authHeaders, refreshCsrfToken]);

  // Workspace-level "Run assessment": assess the assets that need it (unassessed,
  // stale, or degraded) from the currently loaded page. Bounded and sequential so
  // it never floods the backend; duplicate concurrent jobs are prevented server-side.
  const runWorkspaceAssessment = useCallback(async () => {
    if (workspaceAssessing) return;
    setSubmitError('');
    const needsAssessment = assets.filter((a) => {
      const s = String(a.assessment_status || '');
      return a.risk_score === null || a.risk_score === undefined || s === 'not_assessed' || s === 'degraded' || s === 'partial';
    });
    const queue = (needsAssessment.length > 0 ? needsAssessment : assets).slice(0, 25);
    if (queue.length === 0) return;
    setWorkspaceAssessing(true);
    let failures = 0;
    try {
      for (let i = 0; i < queue.length; i += 1) {
        setAssessmentProgress(`Assessing ${i + 1}/${queue.length}…`);
        // eslint-disable-next-line no-await-in-loop
        const ok = await assessOne(queue[i].id);
        if (!ok) failures += 1;
      }
      if (failures > 0) {
        setSubmitError(`Assessment completed with ${failures} asset(s) unable to run. Their state is unchanged.`);
      }
      setPanelRefresh((v) => v + 1);
      await load();
    } catch (error) {
      setSubmitError(classifyApiTransportError('run the assessment', apiUrl, error));
    } finally {
      setWorkspaceAssessing(false);
      setAssessmentProgress('');
    }
  }, [apiUrl, assessOne, assets, load, workspaceAssessing]);

  async function archiveAsset(asset: Asset) {
    if (typeof window !== 'undefined' && !window.confirm(`Archive "${asset.name}"? It will stop being monitored.`)) return;
    setActionLoadingAssetId(asset.id);
    setSubmitError('');
    try {
      const response = await fetch(`${apiUrl}/assets/${asset.id}`, { method: 'DELETE', headers: { ...authHeaders() } });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        setSubmitError((typeof payload?.detail === 'string' && payload.detail) || 'Unable to archive this asset right now.');
        return;
      }
      setSuccessMessage('Asset archived.');
      setDrawerAsset(null);
      setPanelRefresh((v) => v + 1);
      await load();
    } catch (error) {
      setSubmitError(classifyApiTransportError('archive this asset', apiUrl, error));
    } finally {
      setActionLoadingAssetId(null);
    }
  }

  const totalPages = Math.max(1, Math.ceil((pagination.filtered_total || 0) / PAGE_SIZE));
  const hasAnyAssets = pagination.total > 0 || assets.length > 0;

  return (
    <div className="assetsRegistryLayout">
      <div className="assetsRegistryMain">
        {/* ── Page header ──────────────────────────────────────────── */}
        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: '1.25rem', gap: '1rem', flexWrap: 'wrap' }}>
          <div>
            <h1 style={{ margin: 0, fontSize: '1.5rem', fontWeight: 700, color: 'var(--text-primary)' }}>Protected Assets</h1>
            <p style={{ margin: '0.3rem 0 0', color: 'var(--text-secondary)', fontSize: '0.9rem' }}>
              AI risk scoring and monitoring coverage for all protected assets.
            </p>
          </div>
          <button type="button" className="btn btn-primary" onClick={() => { setShowAddModal(true); setSubmitError(''); setSuccessMessage(''); }}>
            Add Asset
          </button>
        </div>

        {/* ── Filters ──────────────────────────────────────────────── */}
        <div style={{ display: 'flex', gap: '0.75rem', marginBottom: '1rem', flexWrap: 'wrap' }}>
          <input
            aria-label="Search assets"
            placeholder="Search assets..."
            value={filters.search}
            onChange={(e) => updateFilter({ search: e.target.value })}
            style={{ flex: '1 1 220px', minWidth: '160px' }}
          />
          <select value={filters.asset_type} onChange={(e) => updateFilter({ asset_type: e.target.value })} aria-label="Filter by asset type" style={{ flex: '0 0 auto', minWidth: '150px' }}>
            <option value="all">All Types</option>
            {RWA_TYPE_OPTIONS.map((opt) => <option key={opt.value} value={opt.value}>{opt.label}</option>)}
          </select>
          <select value={filters.risk_level} onChange={(e) => updateFilter({ risk_level: e.target.value })} aria-label="Filter by risk level" style={{ flex: '0 0 auto', minWidth: '130px' }}>
            <option value="all">All Risk</option>
            <option value="low">Low</option>
            <option value="medium">Medium</option>
            <option value="high">High</option>
            <option value="critical">Critical</option>
          </select>
          <select value={filters.monitoring_health} onChange={(e) => updateFilter({ monitoring_health: e.target.value })} aria-label="Filter by monitoring health" style={{ flex: '0 0 auto', minWidth: '150px' }}>
            <option value="all">All Monitoring</option>
            <option value="healthy">Healthy</option>
            <option value="warning">Warning</option>
            <option value="critical">Critical</option>
            <option value="degraded">Degraded</option>
            <option value="not_configured">Not configured</option>
          </select>
          {facets.networks.length > 0 ? (
            <select value={filters.network} onChange={(e) => updateFilter({ network: e.target.value })} aria-label="Filter by network" style={{ flex: '0 0 auto', minWidth: '140px' }}>
              <option value="all">All Networks</option>
              {facets.networks.map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
          ) : null}
          {facets.custodians.length > 0 ? (
            <select value={filters.custodian} onChange={(e) => updateFilter({ custodian: e.target.value })} aria-label="Filter by custodian" style={{ flex: '0 0 auto', minWidth: '140px' }}>
              <option value="all">All Custodians</option>
              {facets.custodians.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          ) : null}
        </div>

        {loadError ? <p className="statusLine" role="alert" style={{ marginBottom: '1rem' }}>{loadError}</p> : null}
        {submitError && !showAddModal ? <p className="statusLine" role="alert" style={{ marginBottom: '1rem' }}>{submitError}</p> : null}
        {successMessage ? <p className="statusLine successLine" role="status" style={{ marginBottom: '1rem' }}>{successMessage}</p> : null}

        {/* ── Registry table ───────────────────────────────────────── */}
        {loading ? (
          <div className="assetsTableSkeleton" aria-hidden="true">
            {[0, 1, 2, 3, 4].map((i) => <div key={i} className="skelBlock" style={{ height: '44px', marginBottom: '8px' }} />)}
          </div>
        ) : !hasAnyAssets ? (
          <div className="emptyStatePanel" style={{ textAlign: 'center', padding: '4rem 2rem', margin: '0 0 2rem' }}>
            <p style={{ fontSize: '2.5rem', margin: '0 0 1rem', lineHeight: 1 }}>🛡</p>
            <h3 style={{ margin: '0 0 0.5rem', fontSize: '1.1rem', color: 'var(--text-primary)' }}>No protected assets yet</h3>
            <p className="muted" style={{ margin: '0 0 1.5rem', maxWidth: '44ch', marginInline: 'auto' }}>
              Add your first wallet, smart contract, treasury vault, or tokenized RWA to begin monitoring.
            </p>
            <button type="button" className="btn btn-primary" onClick={() => setShowAddModal(true)}>Add Asset</button>
          </div>
        ) : assets.length === 0 ? (
          <div className="emptyStatePanel" style={{ textAlign: 'center', padding: '3rem 2rem' }}>
            <h3 style={{ margin: '0 0 0.5rem', fontSize: '1.05rem', color: 'var(--text-primary)' }}>No assets match these filters</h3>
            <p className="muted" style={{ margin: '0 0 1rem' }}>Try clearing search or filters.</p>
            <button type="button" className="btn btn-secondary" onClick={() => setFilters(DEFAULT_FILTERS)}>Clear filters</button>
          </div>
        ) : (
          <>
            <DataTable
              headers={['Asset Name', 'Asset Type', 'Custodian', 'Network', 'Value (USD)', 'Risk Score', 'Monitoring Health', 'Assessment']}
              compact
            >
              {assets.map((asset) => {
                const monHealth = asset.monitoring_health || 'unknown';
                const valueDisplay = formatUsd(asset.value_usd);
                return (
                  <tr
                    key={asset.id}
                    className="assetRow"
                    tabIndex={0}
                    role="button"
                    aria-label={`Open details for ${asset.name}`}
                    onClick={() => setDrawerAsset(asset)}
                    onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setDrawerAsset(asset); } }}
                  >
                    <td>
                      <strong style={{ display: 'block', color: 'var(--text-primary)' }}>{asset.name}</strong>
                      {asset.identifier ? (
                        <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)', fontFamily: 'monospace' }}>{asset.identifier}</span>
                      ) : null}
                    </td>
                    <td>{rwaTypeLabel(asset.rwa_asset_type, asset.asset_type)}</td>
                    <td>{asset.custodian || <AbsentCell reason="No custodian recorded for this asset." />}</td>
                    <td>{asset.chain_network || <AbsentCell reason="No network recorded for this asset." />}</td>
                    <td title={valueDisplay === '--' ? 'No protected value has been recorded for this asset.' : undefined}>
                      {valueDisplay === '--' ? <AbsentCell reason="No protected value has been recorded for this asset." /> : valueDisplay}
                    </td>
                    <td><RiskBadge score={asset.risk_score} level={asset.risk_level} /></td>
                    <td>
                      <span title={monitoringHealthTooltip(monHealth)}>
                        <StatusPill label={monitoringHealthLabel(monHealth)} variant={monitoringHealthVariant(monHealth)} />
                      </span>
                    </td>
                    <td><AssessmentCell asset={asset} /></td>
                  </tr>
                );
              })}
            </DataTable>

            {/* ── Pagination ─────────────────────────────────────────── */}
            <div className="assetsPagination" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: '1rem', gap: '1rem', flexWrap: 'wrap' }}>
              <span className="muted" style={{ fontSize: '0.82rem' }}>
                Showing {assets.length} of {pagination.filtered_total} filtered · {pagination.total} total
              </span>
              <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
                <button type="button" className="btn btn-secondary" disabled={filters.page <= 1} onClick={() => updateFilter({ page: filters.page - 1 })}>Previous</button>
                <span className="muted" style={{ fontSize: '0.82rem' }}>Page {filters.page} of {totalPages}</span>
                <button type="button" className="btn btn-secondary" disabled={filters.page >= totalPages} onClick={() => updateFilter({ page: filters.page + 1 })}>Next</button>
              </div>
            </div>
          </>
        )}
      </div>

      {/* ── Right-side AI Asset Risk Assessor panel ──────────────────── */}
      <AssetRiskAssessorPanel
        refreshSignal={panelRefresh}
        onRunAssessment={runWorkspaceAssessment}
        assessmentRunning={workspaceAssessing}
        assessmentProgress={assessmentProgress}
        onViewReport={() => updateFilter({ risk_level: 'high' })}
        onFilterAnomalies={() => updateFilter({ risk_level: 'critical' })}
        onFilterGaps={() => updateFilter({ monitoring_health: 'not_configured' })}
      />

      {/* ── Details drawer ───────────────────────────────────────────── */}
      {drawerAsset ? (
        <AssetDetailsDrawer
          asset={drawerAsset}
          apiUrl={apiUrl}
          onClose={() => setDrawerAsset(null)}
          onRunAssessment={() => runAssessment(drawerAsset)}
          onVerify={(asset: Asset, action: string) => runNextAction(asset, action)}
          onArchive={() => archiveAsset(drawerAsset)}
          actionLoading={actionLoadingAssetId === drawerAsset.id}
        />
      ) : null}

      {/* ── Add asset modal ──────────────────────────────────────────── */}
      {showAddModal ? (
        <div className="modalOverlay" role="dialog" aria-modal="true" aria-label="Add protected asset" onClick={() => setShowAddModal(false)}>
          <section className="modalCard" onClick={(e) => e.stopPropagation()}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.75rem' }}>
              <div>
                <p className="sectionEyebrow">Add asset</p>
                <h2 style={{ margin: 0 }}>Register a new protected asset</h2>
              </div>
              <button type="button" className="btn btn-ghost" aria-label="Close" onClick={() => setShowAddModal(false)}>✕</button>
            </div>
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
                  <label htmlFor="asset-rwa-type">Asset type</label>
                  <select
                    id="asset-rwa-type"
                    value={form.rwa_asset_type}
                    onChange={(e) => {
                      const nextType = e.target.value;
                      setForm({ ...form, rwa_asset_type: nextType });
                      // Reserve-backed types reveal the reserve config automatically.
                      if (isReserveBackedRwaType(nextType)) setShowAdvanced(true);
                    }}
                  >
                    {RWA_TYPE_OPTIONS.map((opt) => <option key={opt.value} value={opt.value}>{opt.label}</option>)}
                  </select>
                  <p className="inputHint">
                    {reserveBacked
                      ? 'Reserve-backed type — configure a reserve feed below so coverage can be verified.'
                      : 'Reserve backing is optional for this type.'}
                  </p>
                </div>
                <div className="formField">
                  <label htmlFor="asset-custodian">Custodian</label>
                  <input id="asset-custodian" placeholder="e.g., BNY Mellon" value={form.custodian} onChange={(e) => setForm({ ...form, custodian: e.target.value })} />
                </div>
              </div>
              <div className="buttonRow">
                <div className="formField">
                  <label htmlFor="asset-type">Monitoring type <span aria-hidden="true" className="requiredMark">*</span></label>
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
                    <option value="contract">Smart Contract</option>
                    <option value="wallet">Wallet</option>
                    <option value="treasury-linked asset">Treasury Vault</option>
                    <option value="oracle">Oracle</option>
                    <option value="custody component">Custody component</option>
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
                  <label htmlFor="asset-value">Asset value / valuation (USD)</label>
                  <input id="asset-value" type="number" placeholder="e.g., 1000000" value={form.value_usd} onChange={(e) => setForm({ ...form, value_usd: e.target.value })} />
                </div>
                <div className="formField">
                  <label htmlFor="asset-symbol">Token symbol</label>
                  <input id="asset-symbol" placeholder="e.g., USTB" value={form.token_symbol} onChange={(e) => setForm({ ...form, token_symbol: e.target.value })} />
                </div>
              </div>
              {/* Token metadata (on-chain assets). Wallets don't need a token contract. */}
              {!isWalletType ? (
                <div className="buttonRow">
                  <div className="formField">
                    <label htmlFor="asset-token-contract">Token contract address</label>
                    <input
                      id="asset-token-contract"
                      placeholder="0x… (ERC-20 contract)"
                      value={form.token_contract_address}
                      onChange={(e) => setForm({ ...form, token_contract_address: e.target.value.trim() })}
                    />
                    <p className="inputHint">Metadata (decimals, symbol) can be discovered on-chain after creation when a provider is connected.</p>
                  </div>
                  <div className="formField">
                    <label htmlFor="asset-token-decimals">Token decimals</label>
                    <input id="asset-token-decimals" type="number" min={0} max={36} placeholder="e.g., 18" value={form.token_decimals} onChange={(e) => setForm({ ...form, token_decimals: e.target.value })} />
                  </div>
                </div>
              ) : null}
              {!reserveBacked ? (
                <button type="button" className="secondaryCta" onClick={() => setShowAdvanced((v) => !v)}>
                  {showAdvanced ? 'Hide reserve & source settings' : 'Reserve & source settings'}
                </button>
              ) : (
                <p className="sectionEyebrow" style={{ marginTop: '0.5rem' }}>Reserve &amp; source settings</p>
              )}
              {(showAdvanced || reserveBacked) ? (
                <>
                  <div className="buttonRow">
                    <div className="formField">
                      <label htmlFor="asset-reserve-feed-type">Reserve feed type {reserveBacked ? <span className="requiredMark" aria-hidden="true">*</span> : null}</label>
                      <select id="asset-reserve-feed-type" value={form.reserve_feed_type} onChange={(e) => setForm({ ...form, reserve_feed_type: e.target.value })}>
                        <option value="none">None</option>
                        <option value="manual">Manual attestation</option>
                        <option value="attestation">Attestation report</option>
                        <option value="proof_of_reserve">Proof of reserve</option>
                        <option value="api">Reserve API</option>
                      </select>
                      {reserveBacked && form.reserve_feed_type === 'none' ? (
                        <p className="inputHint">Reserve-backed assets should configure a reserve feed. You can still save now and finish later — it will be marked <strong>Provisioning</strong>.</p>
                      ) : null}
                    </div>
                    <div className="formField">
                      <label htmlFor="asset-reserve-feed-id">Reserve feed identifier / endpoint</label>
                      <input id="asset-reserve-feed-id" placeholder="Feed id or https URL (no secrets)" value={form.reserve_feed_identifier} onChange={(e) => setForm({ ...form, reserve_feed_identifier: e.target.value })} />
                    </div>
                  </div>
                  <div className="buttonRow">
                    <div className="formField">
                      <label htmlFor="asset-reserve-value">Verified reserve value (USD)</label>
                      <input id="asset-reserve-value" type="number" placeholder="Latest attested reserve" value={form.reserve_value_usd} onChange={(e) => setForm({ ...form, reserve_value_usd: e.target.value })} />
                    </div>
                    <div className="formField">
                      <label htmlFor="asset-reserve-interval">Expected update interval (seconds)</label>
                      <input id="asset-reserve-interval" type="number" min={0} placeholder="e.g., 86400" value={form.reserve_update_interval_seconds} onChange={(e) => setForm({ ...form, reserve_update_interval_seconds: e.target.value })} />
                    </div>
                  </div>
                  <div className="formField" style={{ display: 'flex', alignItems: 'center' }}>
                    <label style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
                      <input type="checkbox" checked={form.reserve_verified} onChange={(e) => setForm({ ...form, reserve_verified: e.target.checked })} />
                      Mark reserve value as verified now
                    </label>
                  </div>
                  <div className="buttonRow">
                    <div className="formField">
                      <label htmlFor="asset-price-source">Oracle / price source</label>
                      <input id="asset-price-source" placeholder="e.g., chainlink" value={form.price_source} onChange={(e) => setForm({ ...form, price_source: e.target.value })} />
                    </div>
                    <div className="formField">
                      <label htmlFor="asset-ref-price">Reference price (USD)</label>
                      <input id="asset-ref-price" type="number" placeholder="e.g., 1.00" value={form.reference_price_usd} onChange={(e) => setForm({ ...form, reference_price_usd: e.target.value })} />
                    </div>
                  </div>
                  <div className="buttonRow">
                    <div className="formField">
                      <label htmlFor="asset-supply">Circulating supply (base units)</label>
                      <input id="asset-supply" placeholder="On-chain circulating supply" value={form.circulating_supply} onChange={(e) => setForm({ ...form, circulating_supply: e.target.value })} />
                    </div>
                    <div className="formField">
                      <label htmlFor="asset-owner">Owner team</label>
                      <input id="asset-owner" placeholder="Owner team (e.g., Treasury Ops)" value={form.owner_team} onChange={(e) => setForm({ ...form, owner_team: e.target.value })} />
                    </div>
                  </div>
                  <div className="formField">
                    <label htmlFor="asset-notes">Notes</label>
                    <textarea id="asset-notes" rows={3} placeholder="Optional metadata / notes" value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} />
                  </div>
                </>
              ) : null}
              <div className="buttonRow" style={{ marginTop: '1rem' }}>
                <button type="submit" className="btn btn-primary" disabled={!isFormValid || submitting}>
                  {submitting ? 'Creating asset…' : 'Create asset'}
                </button>
                <button type="button" className="btn btn-ghost" onClick={() => setShowAddModal(false)}>Cancel</button>
              </div>
              {!isFormValid && !submitting
                ? <p className="inputHint" role="status">Create asset is disabled until required fields are valid. {blockedReason}</p>
                : null}
              {submitError ? <p className="statusLine" role="alert">{submitError}</p> : null}
            </form>
          </section>
        </div>
      ) : null}
    </div>
  );
}

/* ── Asset details drawer ───────────────────────────────────────────── */
function AssetDetailsDrawer({
  asset, apiUrl, onClose, onRunAssessment, onVerify, onArchive, actionLoading,
}: {
  asset: Asset;
  apiUrl: string;
  onClose: () => void;
  onRunAssessment: () => Promise<any | null>;
  onVerify: (asset: Asset, action: string) => void;
  onArchive: () => void;
  actionLoading: boolean;
}) {
  const { authHeaders } = usePilotAuth();
  const [detail, setDetail] = useState<any | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const loadDetail = useCallback(async () => {
    setError('');
    try {
      const response = await fetch(`/api/assets/${asset.id}/risk-assessment`, { headers: { ...authHeaders() }, cache: 'no-store' });
      if (!response.ok) {
        setError('Assessment detail is unavailable right now.');
        setLoading(false);
        return;
      }
      setDetail(await response.json());
    } catch {
      setError('Assessment detail is temporarily unavailable.');
    } finally {
      setLoading(false);
    }
  }, [apiUrl, asset.id, authHeaders]);

  useEffect(() => { setLoading(true); void loadDetail(); }, [loadDetail]);

  const assessment = detail?.assessment ?? null;
  const findings: any[] = detail?.findings ?? [];
  const history: any[] = detail?.history ?? [];
  const valuationHistory: any[] = detail?.valuation_history ?? [];
  const monHealth = asset.monitoring_health || assessment?.monitoring_health || 'unknown';
  const nextAction = assetNextAction(asset);

  // Reserve applicability: type-driven (backend flag) or a configured feed. A
  // wallet / non-reserve asset is "not applicable" — never "missing evidence".
  const reserveApplies = Boolean(asset.reserve_required) || (asset.reserve_feed_type && asset.reserve_feed_type !== 'none');
  const reserveStatus = assessment?.reserve_status || (reserveApplies ? 'insufficient_evidence' : 'not_applicable');
  const linkedTargets = Number(asset.monitoring_target_count ?? 0);
  const linkedSystems = Number(asset.monitoring_systems_count ?? 0);
  const priceSource = String(asset.price_source || '').trim();

  return (
    <div className="drawerOverlay" role="dialog" aria-modal="true" aria-label={`${asset.name} details`} onClick={onClose}>
      <aside className="drawerCard" onClick={(e) => e.stopPropagation()}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '1rem' }}>
          <div>
            <h2 style={{ margin: 0, fontSize: '1.15rem' }}>{asset.name}</h2>
            <p className="muted" style={{ margin: '0.2rem 0 0', fontFamily: 'monospace', fontSize: '0.75rem' }}>{asset.identifier}</p>
          </div>
          <button type="button" className="btn btn-ghost" aria-label="Close details" onClick={onClose}>✕</button>
        </div>

        <div className="drawerMetaGrid">
          <div><span className="drawerMetaLabel">Asset type</span><span>{rwaTypeLabel(asset.rwa_asset_type, asset.asset_type)}</span></div>
          <div><span className="drawerMetaLabel">Custodian</span><span>{asset.custodian || '—'}</span></div>
          <div><span className="drawerMetaLabel">Network</span><span>{asset.chain_network || '—'}</span></div>
          <div><span className="drawerMetaLabel">Protected value</span><span>{formatUsd(asset.value_usd)}</span></div>
          <div><span className="drawerMetaLabel">Risk score</span><span><RiskBadge score={asset.risk_score} level={asset.risk_level} /></span></div>
          <div><span className="drawerMetaLabel">Monitoring</span><span title={monitoringHealthTooltip(monHealth)}><StatusPill label={monitoringHealthLabel(monHealth)} variant={monitoringHealthVariant(monHealth)} /></span></div>
        </div>

        {/* Identity & configuration (independent of assessment) */}
        <div className="drawerSection">
          <h3 className="drawerSectionTitle">Configuration</h3>
          {asset.token_contract_address ? (
            <div className="drawerKvRow"><span>Contract address</span><span style={{ fontFamily: 'monospace', fontSize: '0.72rem' }}>{asset.token_contract_address}</span></div>
          ) : null}
          <div className="drawerKvRow">
            <span>Linked monitoring targets</span>
            {linkedTargets > 0
              ? <strong>{linkedTargets} target{linkedTargets === 1 ? '' : 's'} · {linkedSystems} system{linkedSystems === 1 ? '' : 's'}</strong>
              : <DataLabel kind="missing" />}
          </div>
          <div className="drawerKvRow">
            <span>Valuation source</span>
            {priceSource ? <span>{priceSource}</span> : <DataLabel kind="missing" />}
          </div>
          <div className="drawerKvRow">
            <span>Reserve backing</span>
            {reserveApplies
              ? <span title="Reserve feed type configured for this asset.">{String(asset.reserve_feed_type || 'none') === 'none' ? <DataLabel kind="missing" /> : reserveFeedTypeLabel(asset.reserve_feed_type)}</span>
              : <DataLabel kind="not_applicable" />}
          </div>
        </div>

        {loading ? (
          <div className="assetsTableSkeleton"><div className="skelBlock" style={{ height: '80px' }} /><div className="skelBlock" style={{ height: '120px' }} /></div>
        ) : error ? (
          <p className="statusLine" role="alert">{error}</p>
        ) : detail?.status === 'not_assessed' || !assessment ? (
          <div className="drawerSection">
            <p className="muted">
              {detail?.status === 'provisioning'
                ? 'Assessment storage is provisioning. Run an assessment shortly.'
                : 'This asset has not been assessed yet. Run an assessment to compute its risk and monitoring coverage.'}
            </p>
          </div>
        ) : (
          <>
            {/* Reserve coverage — only meaningful when reserve backing applies. */}
            <div className="drawerSection">
              <h3 className="drawerSectionTitle">Reserve coverage</h3>
              <div className="drawerKvRow">
                <span>Status</span>
                <StatusPill label={reserveStatusLabel(reserveStatus)} variant={reserveStatusVariant(reserveStatus)} />
              </div>
              {reserveApplies ? (
                <>
                  <div className="drawerKvRow"><span>Coverage</span><strong>{formatPercent(assessment.reserve_coverage_percent, 1)}</strong></div>
                  <div className="drawerKvRow"><span>Verified reserve</span><span>{formatUsd(assessment.reserve_value_usd)}</span></div>
                  <div className="drawerKvRow"><span>On-chain liability</span><span>{formatUsd(assessment.liability_value_usd)}</span></div>
                </>
              ) : (
                <p className="muted" style={{ margin: '0.35rem 0 0', fontSize: '0.8rem' }}>
                  Reserve backing does not apply to this asset type; it is excluded from reserve coverage.
                </p>
              )}
            </div>

            {/* Confidence & completeness */}
            <div className="drawerSection">
              <h3 className="drawerSectionTitle">Confidence &amp; completeness</h3>
              <div className="drawerKvRow"><span>Confidence</span><strong>{Math.round((Number(assessment.confidence) || 0) * 100)}%</strong></div>
              <div className="drawerKvRow"><span>Data completeness</span><strong>{Math.round((Number(assessment.data_completeness) || 0) * 100)}%</strong></div>
              <p className="drawerFreshness">Assessed {relativeTime(assessment.assessed_at)} · status {assessment.status}{assessment.score_version ? ` · ${assessment.score_version}` : ''}</p>
            </div>

            {/* Risk breakdown — not-applicable dimensions are shown as n/a, never 0. */}
            <div className="drawerSection">
              <h3 className="drawerSectionTitle">Risk score breakdown</h3>
              {(assessment.dimensions ?? []).map((dim: any) => {
                const notApplicable = dim.applicable === false;
                return (
                  <div key={dim.key} className="dimRow" style={notApplicable ? { opacity: 0.55 } : undefined}>
                    <span className="dimName" title={notApplicable ? 'Not applicable to this asset type — excluded from the score.' : undefined}>
                      {String(dim.key).replace(/_/g, ' ')}
                    </span>
                    {notApplicable ? (
                      <span className="muted" style={{ gridColumn: '2 / 4', fontSize: '0.72rem', textAlign: 'right' }}>Not applicable</span>
                    ) : (
                      <>
                        <div className="dimBarTrack"><div className="dimBarFill" style={{ width: `${Math.max(2, Math.min(100, dim.score))}%` }} /></div>
                        <span className="dimScore">{dim.score}</span>
                      </>
                    )}
                  </div>
                );
              })}
            </div>

            {/* Active findings + monitoring gaps */}
            <div className="drawerSection">
              <h3 className="drawerSectionTitle">Active findings ({findings.length})</h3>
              {findings.length === 0 ? (
                <p className="muted">No active findings.</p>
              ) : findings.map((f: any, i: number) => (
                <div key={i} className="findingRow">
                  <StatusPill label={f.severity} variant={f.severity === 'critical' || f.severity === 'high' ? 'danger' : f.severity === 'medium' ? 'warning' : 'neutral'} />
                  <div>
                    <strong>{f.title}</strong>
                    {f.detail ? <p className="muted" style={{ margin: '0.15rem 0 0', fontSize: '0.8rem' }}>{f.detail}</p> : null}
                  </div>
                </div>
              ))}
            </div>

            {/* Last provider observations (labelled by provenance) */}
            {valuationHistory.length > 0 ? (
              <div className="drawerSection">
                <h3 className="drawerSectionTitle">Recent provider observations</h3>
                {valuationHistory.slice(0, 5).map((v: any, i: number) => (
                  <div key={i} className="drawerKvRow">
                    <span>{formatUsd(v.price_usd)} <span className="muted" style={{ fontSize: '0.72rem' }}>· {v.source || 'unknown'}</span></span>
                    <span style={{ display: 'inline-flex', gap: '0.4rem', alignItems: 'center' }}>
                      <DataLabel kind={v.is_estimated ? 'estimated' : 'live'} />
                      <span className="muted" style={{ fontSize: '0.72rem' }}>{relativeTime(v.observed_at)}</span>
                    </span>
                  </div>
                ))}
              </div>
            ) : null}

            {/* Assessment history */}
            {history.length > 1 ? (
              <div className="drawerSection">
                <h3 className="drawerSectionTitle">Assessment history</h3>
                {history.slice(0, 6).map((h: any, i: number) => (
                  <div key={i} className="drawerKvRow">
                    <span><RiskBadge score={h.risk_score} level={h.risk_level} /> <span className="muted" style={{ fontSize: '0.72rem' }}>{h.status}</span></span>
                    <span className="muted" style={{ fontSize: '0.72rem' }}>{relativeTime(h.assessed_at)}</span>
                  </div>
                ))}
              </div>
            ) : null}
          </>
        )}

        <div className="drawerActions">
          <button type="button" className="btn btn-primary" disabled={actionLoading} onClick={() => { void onRunAssessment().then(() => loadDetail()); }}>
            {actionLoading ? 'Running…' : 'Run assessment'}
          </button>
          {nextAction === 'Verify asset' ? (
            <button type="button" className="btn btn-secondary" disabled={actionLoading} onClick={() => onVerify(asset, nextAction)}>Verify asset</button>
          ) : (
            <Link href="/monitoring-sources" prefetch={false} className="btn btn-secondary">Monitoring sources</Link>
          )}
          <button type="button" className="btn btn-ghost" disabled={actionLoading} onClick={onArchive}>Archive asset</button>
        </div>
      </aside>
    </div>
  );
}
