/**
 * Presentation helpers for the External Watchlist (founder console).
 *
 * Pure functions only — no fetch, no React — so every label the page shows can
 * be pinned by a test. The backend is the source of truth for every status,
 * count and finding; nothing here derives a status from raw facts, it only
 * names what the API reported.
 *
 * Truthfulness rules this module keeps (CLAUDE.md §3):
 *   * no events is shown as "No events observed", never as safe or healthy
 *   * no findings is shown as "No findings", never as "clean"
 *   * an unknown time is "Never" / "—", never a fabricated date
 *   * a status the API did not send reads as "Unavailable", never as "Live"
 */

export const WATCHLIST_NOTICE =
  'External Watchlist uses publicly available blockchain data. Monitored organizations have not necessarily authorized, endorsed, or partnered with Decoda Security.';
export const DETAIL_NOTICE =
  'Independent public monitoring. This organization has not necessarily authorized or endorsed Decoda monitoring.';
export const EVIDENCE_DISCLAIMER =
  'This report was generated from publicly available blockchain telemetry through independent monitoring by Decoda Security. It does not imply a commercial relationship, authorization, or endorsement by the monitored organization.';
export const READ_ONLY_NOTE =
  'Read-only. External Watchlist uses public blockchain data only: no wallet connection, private key, signature or write permission is ever requested, and none is accepted.';
export const PUBLIC_INFRASTRUCTURE_BADGE = 'External / Public Infrastructure';
export const EXTERNAL_SOURCE_LABEL = 'External Public Monitoring';

export type WatchlistStatus = 'live' | 'backfilling' | 'paused' | 'degraded' | 'error';
export type PillVariant = 'success' | 'warning' | 'danger' | 'info' | 'neutral' | 'default';

export type NetworkRef = { key: string; label: string; short_label: string; chain_id?: number | null };

export type BackfillSummary = {
  status: string;
  percent: number;
  days_analyzed: number;
  requested_days: number;
  planned?: boolean;
  targets?: number;
  scanned_blocks?: number;
  total_blocks?: number;
  status_reason?: string | null;
  last_error?: string | null;
} | null;

export type WatchlistSummary = {
  id: string;
  name: string;
  slug?: string;
  website_url: string | null;
  description?: string | null;
  networks: NetworkRef[];
  contract_count: number;
  wallet_count: number;
  target_count?: number;
  status: WatchlistStatus | string;
  status_reason?: string | null;
  status_reason_label?: string | null;
  monitoring_enabled?: boolean;
  backfill_days?: number;
  detection_profiles?: string[];
  latest_event: { event_name: string; event_category?: string; observed_at: string | null } | null;
  latest_finding: { title: string; severity?: string; finding_class?: string; observed_at?: string | null; detected_at?: string | null } | null;
  last_scanned_at: string | null;
  backfill?: BackfillSummary;
  converted_workspace_id?: string | null;
  converted_at?: string | null;
  created_at: string | null;
  monitoring_scope?: string;
  execution_authority?: string;
};

export const WATCHLIST_STATUS_LABELS: Record<WatchlistStatus, string> = {
  live: 'Live',
  backfilling: 'Backfilling',
  paused: 'Paused',
  degraded: 'Degraded',
  error: 'Error',
};

export const WATCHLIST_STATUS_OPTIONS: Array<{ value: WatchlistStatus; label: string }> = (
  ['live', 'backfilling', 'paused', 'degraded', 'error'] as WatchlistStatus[]
).map((value) => ({ value, label: WATCHLIST_STATUS_LABELS[value] }));

export function statusLabel(status: string | null | undefined): string {
  return WATCHLIST_STATUS_LABELS[status as WatchlistStatus] ?? 'Unavailable';
}

export function statusVariant(status: string | null | undefined): PillVariant {
  switch (status) {
    case 'live':
      return 'success';
    case 'backfilling':
      return 'info';
    case 'degraded':
      return 'warning';
    case 'error':
      return 'danger';
    default:
      return 'neutral';
  }
}

export const SEVERITY_LABELS: Record<string, string> = { low: 'Low', medium: 'Medium', high: 'High' };

export function severityVariant(severity: string | null | undefined): PillVariant {
  switch (severity) {
    case 'high':
      return 'danger';
    case 'medium':
      return 'warning';
    case 'low':
      return 'info';
    default:
      return 'neutral';
  }
}

export const FINDING_STATUS_OPTIONS: Array<{ value: string; label: string }> = [
  { value: 'new', label: 'New' },
  { value: 'reviewed', label: 'Reviewed' },
  { value: 'expected', label: 'Expected' },
  { value: 'interesting', label: 'Interesting' },
  { value: 'outreach_candidate', label: 'Outreach Candidate' },
  { value: 'dismissed', label: 'Dismissed' },
];

export function findingStatusLabel(status: string | null | undefined): string {
  return FINDING_STATUS_OPTIONS.find((option) => option.value === status)?.label ?? 'Unknown';
}

export const FINDING_CLASS_LABELS: Record<string, string> = {
  review: 'Review',
  administrative_change: 'Administrative change',
  observed_anomaly: 'Observed anomaly',
  unusual_activity: 'Unusual activity',
  privileged_configuration_change: 'Privileged configuration change',
};

export const EVENT_CATEGORY_LABELS: Record<string, string> = {
  access_control: 'Access control',
  upgradeability: 'Upgradeability',
  emergency_control: 'Emergency control',
  token_operation: 'Token operation',
  multisig: 'Multisig',
  oracle: 'Oracle',
};

export const INGEST_SOURCE_LABELS: Record<string, string> = {
  historical_backfill: 'Historical backfill',
  live_poll: 'Live poll',
};

export const TARGET_TYPE_OPTIONS: Array<{ value: string; label: string }> = [
  { value: 'contract', label: 'Contract' },
  { value: 'wallet', label: 'Wallet' },
  { value: 'multisig', label: 'Multisig' },
  { value: 'oracle', label: 'Oracle' },
];

export const BACKFILL_OPTIONS: Array<{ value: number; label: string }> = [
  { value: 0, label: 'None' },
  { value: 7, label: '7 days' },
  { value: 14, label: '14 days' },
  { value: 30, label: '30 days' },
];
export const DEFAULT_BACKFILL_DAYS = 30;

/** Used only until the backend's console configuration has loaded. */
export const DETECTION_PROFILE_FALLBACK: Array<{ key: string; label: string }> = [
  { key: 'privileged_role_changes', label: 'Privileged role changes' },
  { key: 'ownership_changes', label: 'Ownership changes' },
  { key: 'contract_upgrades', label: 'Contract upgrades' },
  { key: 'proxy_admin_changes', label: 'Proxy admin changes' },
  { key: 'pause_unpause', label: 'Pause / unpause' },
  { key: 'multisig_configuration', label: 'Multisig configuration changes' },
  { key: 'mint_burn', label: 'Mint / burn' },
  { key: 'large_transfers', label: 'Large transfers' },
  { key: 'oracle_updates', label: 'Oracle updates/deviations' },
];

// ── formatting ───────────────────────────────────────────────────────────────
export function formatTimestamp(value: string | null | undefined): string {
  if (!value) return '—';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return '—';
  return `${parsed.toISOString().slice(0, 16).replace('T', ' ')} UTC`;
}

export function formatRelativeTime(value: string | null | undefined, now: Date = new Date()): string {
  if (!value) return 'Never';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return 'Never';
  const seconds = Math.round((now.getTime() - parsed.getTime()) / 1000);
  if (seconds < 0) return formatTimestamp(value);
  if (seconds < 45) return 'just now';
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} minute${minutes === 1 ? '' : 's'} ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours} hour${hours === 1 ? '' : 's'} ago`;
  const days = Math.round(hours / 24);
  return `${days} day${days === 1 ? '' : 's'} ago`;
}

export function shortAddress(value: string | null | undefined): string {
  if (!value) return '—';
  return value.length > 14 ? `${value.slice(0, 6)}…${value.slice(-4)}` : value;
}

export function shortHash(value: string | null | undefined): string {
  if (!value) return '—';
  return value.length > 18 ? `${value.slice(0, 10)}…${value.slice(-6)}` : value;
}

export function countLabel(count: number | null | undefined, singular: string, plural: string): string {
  const value = typeof count === 'number' && Number.isFinite(count) ? count : 0;
  return `${value} ${value === 1 ? singular : plural}`;
}

export function networkLabels(networks: NetworkRef[] | null | undefined): string {
  if (!networks || networks.length === 0) return '—';
  return networks.map((network) => network.short_label || network.label || network.key).join(', ');
}

/** "Latest: RoleGranted · 12 minutes ago", or the truthful absence of data. */
export function latestEventLabel(event: WatchlistSummary['latest_event'], now: Date = new Date()): string {
  if (!event || !event.event_name) return 'No events observed yet';
  return `Latest: ${event.event_name} · ${formatRelativeTime(event.observed_at, now)}`;
}

export function latestFindingLabel(finding: WatchlistSummary['latest_finding']): string {
  if (!finding || !finding.title) return 'No findings';
  return finding.title;
}

export function backfillProgressLabel(backfill: BackfillSummary | undefined): string | null {
  if (!backfill) return null;
  const days = backfill.requested_days || 0;
  if (backfill.status === 'pending' && !backfill.planned) {
    return `Queued · ${days} days requested`;
  }
  const analyzed = Math.min(days, Math.floor(backfill.days_analyzed ?? 0));
  return `${analyzed} / ${days} days analyzed`;
}

export function backfillStatusLabel(status: string | null | undefined): string {
  switch (status) {
    case 'pending':
      return 'Pending';
    case 'running':
      return 'Running';
    case 'completed':
      return 'Completed';
    case 'partial':
      return 'Partial';
    case 'failed':
      return 'Failed';
    default:
      return 'Not requested';
  }
}

export function clampPercent(value: number | null | undefined): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) return 0;
  return Math.max(0, Math.min(100, value));
}

// ── Add Public Protocol form ─────────────────────────────────────────────────
export type ProtocolForm = {
  name: string;
  website_url: string;
  network: string;
  address: string;
  label: string;
  target_type: string;
  backfill_days: number;
  detection_profiles: string[];
};

export function emptyProtocolForm(profiles: Array<{ key: string }> = DETECTION_PROFILE_FALLBACK, network = ''): ProtocolForm {
  return {
    name: '',
    website_url: '',
    network,
    address: '',
    label: '',
    target_type: 'contract',
    backfill_days: DEFAULT_BACKFILL_DAYS,
    detection_profiles: profiles.map((profile) => profile.key),
  };
}

export function isEvmAddress(value: string): boolean {
  return /^0x[0-9a-fA-F]{40}$/.test(value.trim());
}

export function validateProtocolForm(form: ProtocolForm): Record<string, string> {
  const errors: Record<string, string> = {};
  if (!form.name.trim()) errors.name = 'Protocol name is required.';
  if (form.name.trim().length > 120) errors.name = 'Protocol name must be at most 120 characters.';
  if (!form.network) errors.network = 'Choose a network.';
  if (form.address.trim() && !isEvmAddress(form.address)) {
    errors.address = 'Enter a 0x-prefixed, 40-hex-character EVM address.';
  }
  if (form.address.trim() && /^0x0{40}$/i.test(form.address.trim())) {
    errors.address = 'The zero address cannot be monitored.';
  }
  if (form.website_url.trim() && /\s/.test(form.website_url.trim())) {
    errors.website_url = 'Website must be a URL without spaces.';
  }
  if (form.detection_profiles.length === 0) errors.detection_profiles = 'Keep at least one detection profile.';
  return errors;
}

export function buildCreatePayload(form: ProtocolForm): Record<string, unknown> {
  const payload: Record<string, unknown> = {
    name: form.name.trim(),
    network: form.network,
    backfill_days: form.backfill_days,
    detection_profiles: form.detection_profiles,
  };
  if (form.website_url.trim()) payload.website_url = form.website_url.trim();
  if (form.address.trim()) {
    payload.address = form.address.trim();
    payload.target_type = form.target_type;
    if (form.label.trim()) payload.label = form.label.trim();
  }
  return payload;
}

// ── API errors ───────────────────────────────────────────────────────────────
export function apiErrorMessage(status: number, payload: unknown, fallback: string): string {
  if (status === 401) return 'Your session is missing or expired. Please sign in again.';
  if (payload && typeof payload === 'object') {
    const detail = (payload as Record<string, unknown>).detail;
    if (detail && typeof detail === 'object' && typeof (detail as Record<string, unknown>).message === 'string') {
      return String((detail as Record<string, unknown>).message);
    }
    if (typeof detail === 'string' && detail.trim()) return detail;
  }
  return `${fallback} (HTTP ${status}).`;
}

export function apiErrorCode(payload: unknown): string | null {
  if (payload && typeof payload === 'object') {
    const detail = (payload as Record<string, unknown>).detail;
    if (detail && typeof detail === 'object' && typeof (detail as Record<string, unknown>).code === 'string') {
      return String((detail as Record<string, unknown>).code);
    }
  }
  return null;
}

// ── prospect report ──────────────────────────────────────────────────────────
export type ProspectReport = {
  heading: string;
  source_label?: string;
  protocol: string;
  protocol_website?: string | null;
  network: string;
  contract: string | null;
  contract_explorer_url?: string | null;
  transaction: string | null;
  transaction_explorer_url?: string | null;
  block_number?: number | null;
  observed_time: string | null;
  observed_change: string;
  change_title?: string | null;
  decoda_analysis: { observed_fact?: string | null; decoda_interpretation?: string | null; operational_authorization?: string | null };
  evidence_verification: {
    status?: string | null;
    evidence_sha256?: string | null;
    manifest_sha256?: string | null;
    signature_algorithm?: string | null;
    public_key_signature?: string | null;
    production_secret?: boolean | null;
  };
  disclaimer: string;
  prepared_by?: string;
  generated_at?: string;
};

export function verificationLabel(verification: ProspectReport['evidence_verification'] | null | undefined): string {
  if (!verification || !verification.status) return 'Not verified';
  if (verification.status !== 'verified') return 'Verification failed';
  if (verification.production_secret === false) {
    return 'Verified (development signing key — not valid for evidentiary use)';
  }
  return verification.public_key_signature === 'valid'
    ? 'Verified (HMAC-SHA256 seal and Ed25519 signature)'
    : 'Verified (HMAC-SHA256 seal)';
}

export function prospectReportRows(report: ProspectReport): Array<{ label: string; value: string }> {
  return [
    { label: 'Protocol', value: report.protocol },
    { label: 'Network', value: report.network },
    { label: 'Contract', value: report.contract ?? '—' },
    { label: 'Transaction', value: report.transaction ?? '—' },
    { label: 'Block', value: report.block_number != null ? String(report.block_number) : '—' },
    { label: 'Observed time', value: formatTimestamp(report.observed_time) },
    { label: 'Observed change', value: report.observed_change },
    { label: 'Evidence verification', value: verificationLabel(report.evidence_verification) },
    { label: 'Evidence SHA-256', value: report.evidence_verification?.evidence_sha256 ?? '—' },
  ];
}

export function prospectReportText(report: ProspectReport): string {
  const lines = [report.heading, ''];
  for (const row of prospectReportRows(report)) lines.push(`${row.label}: ${row.value}`);
  lines.push('', 'Decoda analysis');
  lines.push(`Observed fact: ${report.decoda_analysis.observed_fact ?? '—'}`);
  lines.push(`Decoda interpretation: ${report.decoda_analysis.decoda_interpretation ?? '—'}`);
  lines.push(`Operational authorization: ${report.decoda_analysis.operational_authorization ?? '—'}`);
  lines.push('', report.disclaimer, '', `Prepared by ${report.prepared_by ?? 'Decoda Security'} · ${formatTimestamp(report.generated_at ?? null)}`);
  return lines.join('\n');
}

function escapeXml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&apos;');
}

/** Greedy word wrap by character budget (monospace-safe for hashes). */
export function wrapText(value: string, maxChars: number): string[] {
  const words = String(value ?? '').split(/\s+/).filter(Boolean);
  const lines: string[] = [];
  let current = '';
  for (const word of words) {
    if (word.length > maxChars) {
      if (current) {
        lines.push(current);
        current = '';
      }
      for (let index = 0; index < word.length; index += maxChars) lines.push(word.slice(index, index + maxChars));
      continue;
    }
    const next = current ? `${current} ${word}` : word;
    if (next.length > maxChars) {
      lines.push(current);
      current = word;
    } else {
      current = next;
    }
  }
  if (current) lines.push(current);
  return lines.length ? lines : ['—'];
}

/**
 * The prospect report as a self-contained SVG image, for "Export Screenshot".
 *
 * Built from the report's own fields — never from the DOM — so the exported
 * image contains exactly what the shareable report contains and nothing an
 * internal screen shows next to it. Rasterised to PNG in the browser.
 */
export function buildProspectReportSvg(report: ProspectReport): { svg: string; width: number; height: number } {
  const width = 960;
  const pad = 48;
  const labelWidth = 190;
  const valueChars = 72;
  const paragraphChars = 104;
  const parts: string[] = [];
  let y = pad;
  const text = (x: number, yy: number, value: string, attrs: string) =>
    parts.push(`<text x="${x}" y="${yy}" ${attrs}>${escapeXml(value)}</text>`);

  text(pad, y + 4, (report.source_label ?? EXTERNAL_SOURCE_LABEL).toUpperCase(), 'fill="#7daeff" font-size="12" font-weight="700" letter-spacing="1.2"');
  y += 34;
  text(pad, y, report.heading, 'fill="#f8fafc" font-size="26" font-weight="700"');
  y += 22;
  if (report.change_title) {
    text(pad, y + 8, report.change_title, 'fill="#cbd5e1" font-size="15"');
    y += 26;
  }
  y += 14;
  for (const row of prospectReportRows(report)) {
    const lines = wrapText(row.value, valueChars);
    text(pad, y, row.label, 'fill="#94a3b8" font-size="13" font-weight="600"');
    lines.forEach((line, index) =>
      text(pad + labelWidth, y + index * 19, line, 'fill="#f8fafc" font-size="13" font-family="ui-monospace, Menlo, Consolas, monospace"'),
    );
    y += Math.max(1, lines.length) * 19 + 10;
  }
  y += 10;
  text(pad, y, 'DECODA ANALYSIS', 'fill="#7daeff" font-size="12" font-weight="700" letter-spacing="1.2"');
  y += 24;
  const sections: Array<[string, string | null | undefined]> = [
    ['Observed fact', report.decoda_analysis.observed_fact],
    ['Decoda interpretation', report.decoda_analysis.decoda_interpretation],
    ['Operational authorization', report.decoda_analysis.operational_authorization],
  ];
  for (const [label, value] of sections) {
    text(pad, y, label, 'fill="#cbd5e1" font-size="13" font-weight="700"');
    y += 19;
    for (const line of wrapText(value ?? '—', paragraphChars)) {
      text(pad, y, line, 'fill="#e2e8f0" font-size="13"');
      y += 18;
    }
    y += 10;
  }
  y += 6;
  parts.push(`<line x1="${pad}" y1="${y}" x2="${width - pad}" y2="${y}" stroke="#263449" stroke-width="1"/>`);
  y += 24;
  for (const line of wrapText(report.disclaimer, paragraphChars + 6)) {
    text(pad, y, line, 'fill="#94a3b8" font-size="12"');
    y += 17;
  }
  y += 8;
  text(pad, y, `Prepared by ${report.prepared_by ?? 'Decoda Security'} · ${formatTimestamp(report.generated_at ?? null)}`, 'fill="#94a3b8" font-size="12"');
  const height = y + pad;
  const svg = [
    `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" font-family="Inter, system-ui, Arial, sans-serif">`,
    `<rect width="${width}" height="${height}" fill="#0f1a2b"/>`,
    `<rect x="0" y="0" width="6" height="${height}" fill="#2563eb"/>`,
    ...parts,
    '</svg>',
  ].join('');
  return { svg, width, height };
}
