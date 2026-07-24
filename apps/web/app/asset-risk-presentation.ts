// Presentation helpers for the Protected Asset Registry (Screen 3).
//
// Pure, framework-free mappers so risk badges, monitoring-health pills, RWA type
// labels, and reserve-status copy stay consistent between the table, the details
// drawer, and the AI Asset Risk Assessor panel — and are unit-testable without a
// browser. Nothing here invents data; callers pass canonical backend values.

import type { PillVariant } from './components/ui-primitives';

export type RiskLevel = 'low' | 'medium' | 'high' | 'critical' | 'unassessed';

// Canonical 0-100 scale where HIGHER means MORE risk (see backend asset-risk-v1).
export const RISK_SCORE_TOOLTIP =
  'Higher values represent greater operational, reserve, market, and monitoring risk. ' +
  'Scored 0-100: 0-29 low, 30-59 medium, 60-79 high, 80-100 critical.';

export function riskLevelForScore(score: number | null | undefined): RiskLevel {
  if (score === null || score === undefined || Number.isNaN(Number(score))) return 'unassessed';
  const n = Number(score);
  if (n >= 80) return 'critical';
  if (n >= 60) return 'high';
  if (n >= 30) return 'medium';
  return 'low';
}

export function riskLevelLabel(level: RiskLevel): string {
  switch (level) {
    case 'critical': return 'Critical';
    case 'high': return 'High';
    case 'medium': return 'Medium';
    case 'low': return 'Low';
    default: return 'Not assessed';
  }
}

export function riskLevelVariant(level: RiskLevel): PillVariant {
  switch (level) {
    case 'critical':
    case 'high': return 'danger';
    case 'medium': return 'warning';
    case 'low': return 'success';
    default: return 'neutral';
  }
}

// Truthful monitoring-health states. "healthy" is only shown when the backend
// says so; every other state fails toward caution.
export type MonitoringHealth =
  | 'healthy' | 'warning' | 'critical' | 'degraded' | 'provisioning' | 'not_configured' | 'unknown';

export function monitoringHealthLabel(health: string | null | undefined): string {
  switch ((health || '').toLowerCase()) {
    case 'healthy': return 'Healthy';
    case 'warning': return 'Warning';
    case 'critical': return 'Critical';
    case 'degraded': return 'Degraded';
    case 'provisioning': return 'Provisioning';
    case 'not_configured': return 'Not configured';
    default: return 'Unknown';
  }
}

export function monitoringHealthVariant(health: string | null | undefined): PillVariant {
  switch ((health || '').toLowerCase()) {
    case 'healthy': return 'success';
    case 'warning':
    case 'degraded': return 'warning';
    case 'critical': return 'danger';
    case 'provisioning': return 'info';
    default: return 'neutral';
  }
}

export function reserveStatusLabel(status: string | null | undefined): string {
  switch ((status || '').toLowerCase()) {
    case 'healthy': return 'Healthy';
    case 'warning': return 'Warning';
    case 'critical': return 'Critical';
    case 'over_collateralized': return 'Over-collateralized';
    case 'insufficient_evidence': return 'Insufficient evidence';
    case 'not_required': return 'Not required';
    default: return 'Unknown';
  }
}

export function reserveStatusVariant(status: string | null | undefined): PillVariant {
  switch ((status || '').toLowerCase()) {
    case 'healthy': return 'success';
    case 'warning':
    case 'over_collateralized': return 'warning';
    case 'critical': return 'danger';
    case 'insufficient_evidence': return 'neutral';
    case 'not_required': return 'info';
    default: return 'neutral';
  }
}

const RWA_TYPE_LABELS: Record<string, string> = {
  tokenized_treasury: 'Tokenized Treasury',
  stablecoin: 'Stablecoin',
  money_market_fund: 'Money Market Fund',
  fund_share: 'Fund Share',
  corporate_bond: 'Corporate Bond',
  private_credit: 'Private Credit',
  invoice_financing: 'Invoice Financing',
  commodity: 'Commodity',
  real_estate: 'Real Estate',
  other: 'Other',
};

export const RWA_TYPE_OPTIONS = Object.entries(RWA_TYPE_LABELS).map(([value, label]) => ({ value, label }));

export function rwaTypeLabel(value: string | null | undefined, fallback?: string | null): string {
  const key = (value || '').toLowerCase();
  if (RWA_TYPE_LABELS[key]) return RWA_TYPE_LABELS[key];
  if (fallback) return fallback.replace(/[-_]/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
  return 'Unclassified';
}

export function formatUsd(value: number | string | null | undefined): string {
  if (value === null || value === undefined || value === '') return '--';
  const n = Number(value);
  if (Number.isNaN(n)) return '--';
  if (Math.abs(n) >= 1_000_000_000) return `$${(n / 1_000_000_000).toFixed(2)}B`;
  if (Math.abs(n) >= 1_000_000) return `$${(n / 1_000_000).toFixed(2)}M`;
  if (Math.abs(n) >= 1_000) return `$${(n / 1_000).toFixed(1)}K`;
  return `$${n.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
}

export function formatPercent(value: number | string | null | undefined, digits = 0): string {
  if (value === null || value === undefined || value === '') return '--';
  const n = Number(value);
  if (Number.isNaN(n)) return '--';
  return `${n.toFixed(digits)}%`;
}

// "5m ago" / "2h ago" from an ISO timestamp; truthful "never" when absent.
export function relativeTime(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return 'never';
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return 'never';
  const secs = Math.max(0, Math.floor((now - t) / 1000));
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}
