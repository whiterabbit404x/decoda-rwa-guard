export function statusBadgeClass(status: string): string {
  if (status === 'healthy') return 'statusBadge statusBadge-live';
  if (status === 'degraded') return 'statusBadge statusBadge-degraded';
  if (status === 'failing') return 'statusBadge statusBadge-offline';
  return 'statusBadge statusBadge-unavailable';
}

export function statusLabel(status: string): string {
  if (status === 'healthy') return 'Operational';
  if (status === 'degraded') return 'Degraded';
  if (status === 'failing') return 'Failing';
  return 'Unavailable';
}

export function overallLabel(status: string): string {
  if (status === 'healthy') return 'All Systems Operational';
  if (status === 'degraded') return 'Degraded';
  if (status === 'failing') return 'Action Required';
  return 'Unavailable';
}

export function healthIconClass(status: string): string {
  if (status === 'healthy') return 'healthIcon healthIconLive';
  if (status === 'failing') return 'healthIcon healthIconOffline';
  return 'healthIcon healthIconDegraded';
}

export function healthIconGlyph(status: string): string {
  if (status === 'healthy') return '✓';
  if (status === 'failing') return '✕';
  return '!';
}

export function severityBadgeClass(severity: string): string {
  if (severity === 'critical' || severity === 'high') return 'statusBadge statusBadge-offline';
  if (severity === 'medium') return 'statusBadge statusBadge-degraded';
  return 'statusBadge statusBadge-unavailable';
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return '—';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString();
}

export function formatShortTime(value: string | null | undefined): string {
  if (!value) return '—';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleTimeString();
}

export function diagnosisVariant(
  diagnosis: string | undefined,
): 'healthy' | 'degraded' | 'failing' {
  if (!diagnosis) return 'degraded';
  const lower = diagnosis.toLowerCase();
  if (lower.includes('operational') || lower.includes('all monitored')) return 'healthy';
  if (
    lower.includes('failing') ||
    lower.includes('failed') ||
    lower.includes('unavailable') ||
    lower.includes('not configured')
  ) {
    return 'failing';
  }
  return 'degraded';
}
