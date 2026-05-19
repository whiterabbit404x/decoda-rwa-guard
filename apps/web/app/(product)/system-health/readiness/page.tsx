'use client';

import Link from 'next/link';
import { useCallback, useEffect, useState } from 'react';

import { usePilotAuth } from 'app/pilot-auth-context';

export const dynamic = 'force-dynamic';

// ---------------------------------------------------------------------------
// Types matching the /admin/readiness API response (production_readiness.py)
// ---------------------------------------------------------------------------

type CheckStatus = 'pass' | 'warn' | 'fail' | 'unavailable' | 'partial' | string;

type ReadinessCheck = {
  key: string;
  label: string;
  status: CheckStatus;
  reason: string;
  source: string;
  evidence: Record<string, unknown>;
  last_seen_at: string | null;
};

type CategoryName =
  | 'Platform'
  | 'Runtime'
  | 'Workflow'
  | 'Evidence & Export'
  | 'Integrations'
  | 'Security';

type ReadinessReport = {
  generated_at: string;
  ready_for_pilot: boolean;
  ready_for_paid_public_launch: boolean;
  blocking_reasons: string[];
  warnings: string[];
  categories: Record<CategoryName, ReadinessCheck[]>;
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function humanize(s: string): string {
  return s
    .replace(/_/g, ' ')
    .replace(/-/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

function formatTs(value: string | null | undefined): string {
  if (!value) return '';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    timeZoneName: 'short',
  });
}

function formatRelative(value: string | null | undefined): string {
  if (!value) return '';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  const diffMs = Date.now() - d.getTime();
  const diffSec = Math.floor(diffMs / 1000);
  if (diffSec < 60) return `${diffSec}s ago`;
  const diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  return `${Math.floor(diffHr / 24)}d ago`;
}

// Derive a single per-category rollup status
function categoryRollup(checks: ReadinessCheck[]): CheckStatus {
  if (checks.some((c) => c.status === 'fail')) return 'fail';
  if (checks.some((c) => c.status === 'warn')) return 'warn';
  if (checks.every((c) => c.status === 'pass')) return 'pass';
  return 'unavailable';
}

// CSS class for status dot (small circle)
function dotClass(status: CheckStatus): string {
  if (status === 'pass') return 'rdDot rdDot-pass';
  if (status === 'warn' || status === 'partial') return 'rdDot rdDot-warn';
  if (status === 'fail') return 'rdDot rdDot-fail';
  return 'rdDot rdDot-unavail';
}

// StatusBadge for category pill
function statusPillClass(status: CheckStatus): string {
  if (status === 'pass') return 'statusBadge statusBadge-live';
  if (status === 'warn' || status === 'partial') return 'statusBadge statusBadge-degraded';
  if (status === 'fail') return 'statusBadge statusBadge-offline';
  return 'statusBadge statusBadge-unavailable';
}

function statusPillLabel(status: CheckStatus): string {
  if (status === 'pass') return 'PASS';
  if (status === 'warn' || status === 'partial') return 'WARN';
  if (status === 'fail') return 'FAIL';
  return 'UNAVAILABLE';
}

// Derive a human-readable check value to show in the "evidence" column
function checkValueLabel(check: ReadinessCheck): string {
  const { status, evidence } = check;
  // Special cases: counts, sources, etc.
  if (typeof evidence?.count === 'number') return String(evidence.count);
  if (typeof evidence?.value === 'string') return evidence.value as string;
  if (typeof evidence?.evidence_source === 'string') {
    const src = evidence.evidence_source as string;
    return src.toUpperCase();
  }
  // Fallback to status label
  if (status === 'pass') return 'PASS';
  if (status === 'warn' || status === 'partial') return 'WARN';
  if (status === 'fail') return 'FAIL';
  return status ? status.toUpperCase() : 'UNAVAILABLE';
}

// Color for the value label
function valueColor(check: ReadinessCheck): string {
  const src =
    typeof check.evidence?.evidence_source === 'string'
      ? (check.evidence.evidence_source as string)
      : '';
  if (src === 'simulator') return '#ffd280'; // warn/amber for simulator
  if (check.status === 'pass') return '#7ff0b4';
  if (check.status === 'warn' || check.status === 'partial') return '#ffd280';
  if (check.status === 'fail') return '#ffb3b3';
  return '#dbe5ff';
}

// Count helper across all categories
function countChecks(
  categories: Record<string, ReadinessCheck[]>,
  predicate: (c: ReadinessCheck) => boolean,
): number {
  return Object.values(categories)
    .flat()
    .filter(predicate).length;
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

const CATEGORY_ICONS: Record<string, string> = {
  Platform: '🖥',
  Runtime: '⚙',
  Workflow: '🔗',
  'Evidence & Export': '🔒',
  Integrations: '🔌',
  Security: '🛡',
};

const CATEGORY_ORDER: CategoryName[] = [
  'Platform',
  'Runtime',
  'Workflow',
  'Evidence & Export',
  'Integrations',
  'Security',
];

function CheckRow({ check }: { check: ReadinessCheck }) {
  const val = checkValueLabel(check);
  const color = valueColor(check);
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: '0.5rem',
        padding: '0.45rem 0',
        borderBottom: '1px solid rgba(48,54,61,0.5)',
        fontSize: '0.82rem',
      }}
    >
      <span className={dotClass(check.status)} aria-hidden="true" />
      <span style={{ flex: 1, color: '#c9d1d9' }}>{check.label}</span>
      {check.last_seen_at && (
        <span style={{ color: '#5a6478', fontSize: '0.75rem', whiteSpace: 'nowrap' }}>
          {formatRelative(check.last_seen_at)}
        </span>
      )}
      <span
        style={{
          color,
          fontWeight: 700,
          fontSize: '0.75rem',
          minWidth: '5rem',
          textAlign: 'right',
          whiteSpace: 'nowrap',
        }}
      >
        {val}
      </span>
    </div>
  );
}

function CategoryCard({ name, checks }: { name: string; checks: ReadinessCheck[] }) {
  const rollup = categoryRollup(checks);
  const icon = CATEGORY_ICONS[name] ?? '●';
  return (
    <article className="dataCard" style={{ display: 'flex', flexDirection: 'column', gap: 0 }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          marginBottom: '0.75rem',
        }}
      >
        <p className="sectionEyebrow" style={{ margin: 0, display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
          <span aria-hidden="true">{icon}</span>
          {name}
        </p>
        <span className={statusPillClass(rollup)} style={{ fontSize: '0.7rem', padding: '0.2rem 0.55rem' }}>
          {statusPillLabel(rollup)}
        </span>
      </div>
      <div>
        {checks.map((c) => (
          <CheckRow key={c.key} check={c} />
        ))}
      </div>
    </article>
  );
}

function BlockerList({
  title,
  items,
  variant,
}: {
  title: string;
  items: string[];
  variant: 'blocker' | 'warning';
}) {
  const accentColor = variant === 'blocker' ? '#ffb3b3' : '#ffd280';
  const iconColor = variant === 'blocker' ? '#ff6b6b' : '#ffd280';
  const dotLabel = variant === 'blocker' ? '●' : '▲';
  return (
    <article className="dataCard" style={{ flex: 1, minWidth: 0 }}>
      <p className="sectionEyebrow" style={{ marginBottom: '0.6rem' }}>
        <span style={{ color: iconColor }}>{dotLabel}</span> {title} ({items.length})
      </p>
      {items.length === 0 ? (
        <p style={{ color: '#5a6478', fontSize: '0.82rem' }}>None</p>
      ) : (
        <ul style={{ margin: 0, paddingLeft: '1.1rem', listStyle: 'disc' }}>
          {items.slice(0, 6).map((r) => (
            <li key={r} style={{ color: accentColor, fontSize: '0.82rem', marginBottom: '0.3rem' }}>
              {humanize(r)}
            </li>
          ))}
          {items.length > 6 && (
            <li style={{ color: '#5a6478', fontSize: '0.78rem' }}>+{items.length - 6} more</li>
          )}
        </ul>
      )}
    </article>
  );
}

function ReadinessCriteria() {
  const pilotCriteria = [
    'Database, auth/session, workspace scope',
    'At least one protected asset',
    'At least one reporting system (or explicit setup required state)',
    'Worker heartbeat, poll & telemetry reporting',
    'Evidence/export capability truthfully reported',
    'No contradiction flags',
  ];
  const paidCriteria = [
    'Billing provider configured (or paid UI disabled)',
    'Email provider configured',
    'Redis/Cache configured (or intentionally disabled)',
    'Production URLs configured',
    'Provider health known',
    'Evidence source is live (not simulator)',
  ];
  return (
    <article className="dataCard" style={{ gridColumn: 'span 1' }}>
      <p className="sectionEyebrow" style={{ marginBottom: '0.75rem' }}>Readiness Gate Criteria</p>
      <p style={{ fontSize: '0.8rem', color: '#8b949e', marginBottom: '0.5rem', fontWeight: 600 }}>
        Pilot launch requires:
      </p>
      <ul style={{ margin: '0 0 1rem', paddingLeft: '1.1rem', listStyle: 'disc' }}>
        {pilotCriteria.map((c) => (
          <li key={c} style={{ color: '#c9d1d9', fontSize: '0.78rem', marginBottom: '0.25rem' }}>
            {c}
          </li>
        ))}
      </ul>
      <p style={{ fontSize: '0.8rem', color: '#8b949e', marginBottom: '0.5rem', fontWeight: 600 }}>
        Paid public launch requires pilot readiness plus:
      </p>
      <ul style={{ margin: 0, paddingLeft: '1.1rem', listStyle: 'disc' }}>
        {paidCriteria.map((c) => (
          <li key={c} style={{ color: '#c9d1d9', fontSize: '0.78rem', marginBottom: '0.25rem' }}>
            {c}
          </li>
        ))}
      </ul>
    </article>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function ProductionReadinessPage() {
  const { apiUrl, authHeaders, loading: authLoading } = usePilotAuth();

  const [report, setReport] = useState<ReadinessReport | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  const load = useCallback(async () => {
    if (!apiUrl) return;
    setLoading(true);
    setLoadError(null);
    try {
      const res = await fetch(`${apiUrl}/admin/readiness`, {
        cache: 'no-store',
        headers: authHeaders(),
      });
      if (res.status === 401 || res.status === 403) {
        setLoadError('admin_access_required');
        return;
      }
      if (!res.ok) {
        setLoadError('api_error');
        return;
      }
      const data = (await res.json()) as ReadinessReport;
      setReport(data);
      setLastUpdated(new Date());
    } catch {
      setLoadError('api_error');
    } finally {
      setLoading(false);
    }
  }, [apiUrl, authHeaders]);

  useEffect(() => {
    if (!authLoading && apiUrl) void load();
  }, [authLoading, apiUrl, load]);

  // Derived summary counts
  const totalChecks = report
    ? Object.values(report.categories).flat().length
    : 0;
  const passedChecks = report
    ? countChecks(report.categories, (c) => c.status === 'pass')
    : 0;
  const warnChecks = report
    ? countChecks(report.categories, (c) => c.status === 'warn' || c.status === 'partial')
    : 0;
  const failChecks = report
    ? countChecks(report.categories, (c) => c.status === 'fail')
    : 0;
  const unavailChecks = report
    ? countChecks(
        report.categories,
        (c) => c.status === 'unavailable' || (!c.status),
      )
    : 0;

  const overallLabel = report
    ? report.ready_for_pilot
      ? 'READY FOR PILOT'
      : 'NOT READY'
    : '—';
  const overallColor = report
    ? report.ready_for_pilot
      ? '#7ff0b4'
      : '#ffb3b3'
    : '#8b949e';
  const overallSubLabel = report
    ? report.ready_for_paid_public_launch
      ? 'Ready for paid public launch'
      : `Not ready for paid public launch${report.blocking_reasons.length ? ` · ${report.blocking_reasons.length} blocking issue${report.blocking_reasons.length !== 1 ? 's' : ''}` : ''}`
    : 'Loading readiness data…';

  return (
    <main className="productPage">
      {/* Inline styles for readiness-specific classes */}
      <style>{`
        .rdDot {
          display: inline-block;
          width: 8px;
          height: 8px;
          border-radius: 50%;
          flex-shrink: 0;
        }
        .rdDot-pass   { background: #46c48c; }
        .rdDot-warn   { background: #f2cc60; }
        .rdDot-fail   { background: #f85149; }
        .rdDot-unavail { background: #5a6478; }
        .rdCategoryGrid {
          display: grid;
          grid-template-columns: repeat(3, 1fr);
          gap: 1rem;
        }
        @media (max-width: 1100px) {
          .rdCategoryGrid { grid-template-columns: repeat(2, 1fr); }
        }
        @media (max-width: 700px) {
          .rdCategoryGrid { grid-template-columns: 1fr; }
        }
      `}</style>

      {/* ── Breadcrumb + Header ─────────────────────────────────────── */}
      <section className="hero compactHero" style={{ paddingBottom: '1rem' }}>
        <div style={{ flex: 1 }}>
          <nav aria-label="Breadcrumb" style={{ fontSize: '0.78rem', color: '#5a6478', marginBottom: '0.5rem' }}>
            <span>Admin</span>
            <span style={{ margin: '0 0.3rem' }}>/</span>
            <Link href="/system-health" style={{ color: '#58a6ff', textDecoration: 'none' }}>
              System Health
            </Link>
            <span style={{ margin: '0 0.3rem' }}>/</span>
            <span style={{ color: '#c9d1d9' }}>Production Readiness</span>
          </nav>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
            <h1 style={{ margin: 0 }}>Production Readiness</h1>
            <span
              style={{
                fontSize: '0.7rem',
                fontWeight: 700,
                background: 'rgba(88,166,255,0.12)',
                color: '#58a6ff',
                border: '1px solid rgba(88,166,255,0.25)',
                borderRadius: '4px',
                padding: '0.15rem 0.4rem',
                letterSpacing: '0.05em',
              }}
            >
              BETA
            </span>
          </div>
          <p className="lede" style={{ marginTop: '0.35rem' }}>
            Internal launch validation for Decoda RWA Guard
          </p>
        </div>
        <div
          style={{
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'flex-end',
            gap: '0.5rem',
          }}
        >
          <p style={{ margin: 0, fontSize: '0.78rem', color: '#5a6478' }}>
            Last updated:{' '}
            {lastUpdated
              ? lastUpdated.toLocaleString(undefined, {
                  month: 'short',
                  day: 'numeric',
                  year: 'numeric',
                  hour: '2-digit',
                  minute: '2-digit',
                  timeZoneName: 'short',
                })
              : 'Never'}
          </p>
          <button
            className="secondaryCta"
            style={{ fontSize: '0.85rem' }}
            onClick={() => void load()}
            disabled={loading}
          >
            {loading ? 'Loading…' : '↻ Refresh'}
          </button>
        </div>
      </section>

      {/* ── Error / Loading states ───────────────────────────────────── */}
      {(loading || authLoading) && !report && (
        <section className="banner" role="status" style={{ marginBottom: '1rem' }}>
          Loading readiness…
        </section>
      )}
      {loadError === 'admin_access_required' && (
        <section className="banner banner-offline" role="alert" style={{ marginBottom: '1rem' }}>
          <strong>Admin access required.</strong> You must be signed in as a workspace admin to view
          production readiness.
        </section>
      )}
      {loadError === 'api_error' && (
        <section className="banner banner-degraded" role="alert" style={{ marginBottom: '1rem' }}>
          <strong>Unable to load readiness checks.</strong> The readiness API did not respond. Check
          API health and retry.
        </section>
      )}

      {report && (
        <>
          {/* ── Overall Summary Card ─────────────────────────────────── */}
          <section className="dataCard" style={{ marginBottom: '1rem' }}>
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: 'auto 1fr 1fr 1fr 1fr 1fr 1fr 1fr',
                gap: '1rem',
                alignItems: 'center',
              }}
            >
              {/* Overall status block */}
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                <div
                  style={{
                    width: '48px',
                    height: '48px',
                    borderRadius: '50%',
                    background: report.ready_for_pilot
                      ? 'rgba(70,196,140,0.15)'
                      : 'rgba(248,81,73,0.15)',
                    border: `2px solid ${report.ready_for_pilot ? 'rgba(70,196,140,0.4)' : 'rgba(248,81,73,0.4)'}`,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    fontSize: '1.2rem',
                    flexShrink: 0,
                  }}
                  aria-hidden="true"
                >
                  {report.ready_for_pilot ? '✓' : '✗'}
                </div>
                <div>
                  <p className="sectionEyebrow" style={{ margin: '0 0 0.15rem' }}>
                    Overall Readiness
                  </p>
                  <p
                    style={{
                      margin: 0,
                      fontWeight: 800,
                      fontSize: '1rem',
                      color: overallColor,
                      lineHeight: 1.2,
                    }}
                  >
                    {overallLabel}
                  </p>
                  <p style={{ margin: 0, fontSize: '0.72rem', color: '#8b949e' }}>
                    {overallSubLabel}
                  </p>
                </div>
              </div>

              {/* Pilot */}
              <div style={{ borderLeft: '1px solid #30363d', paddingLeft: '1rem' }}>
                <p className="sectionEyebrow" style={{ margin: '0 0 0.25rem' }}>Pilot Readiness</p>
                <p
                  style={{
                    margin: 0,
                    fontWeight: 800,
                    fontSize: '1.4rem',
                    color: report.ready_for_pilot ? '#7ff0b4' : '#ffb3b3',
                  }}
                >
                  {report.ready_for_pilot ? 'YES' : 'NO'}
                </p>
                <p style={{ margin: 0, fontSize: '0.72rem', color: '#5a6478' }}>
                  {report.ready_for_pilot ? 'Updated just now' : `${report.blocking_reasons.length} blocker${report.blocking_reasons.length !== 1 ? 's' : ''}`}
                </p>
              </div>

              {/* Paid launch */}
              <div style={{ borderLeft: '1px solid #30363d', paddingLeft: '1rem' }}>
                <p className="sectionEyebrow" style={{ margin: '0 0 0.25rem' }}>Paid Public Launch</p>
                <p
                  style={{
                    margin: 0,
                    fontWeight: 800,
                    fontSize: '1.4rem',
                    color: report.ready_for_paid_public_launch ? '#7ff0b4' : '#ffb3b3',
                  }}
                >
                  {report.ready_for_paid_public_launch ? 'YES' : 'NO'}
                </p>
                <p style={{ margin: 0, fontSize: '0.72rem', color: '#5a6478' }}>
                  {report.ready_for_paid_public_launch
                    ? 'Ready for paid customers'
                    : report.blocking_reasons.length > 0
                    ? `${report.blocking_reasons.length} blocking issue${report.blocking_reasons.length !== 1 ? 's' : ''}`
                    : 'Not yet ready'}
                </p>
              </div>

              {/* Metric tiles */}
              {(
                [
                  ['Total Checks', totalChecks, '#c9d1d9'],
                  ['Passed', passedChecks, '#7ff0b4'],
                  ['Warnings', warnChecks, '#ffd280'],
                  ['Failed', failChecks, '#ffb3b3'],
                  ['Unavailable', unavailChecks, '#dbe5ff'],
                ] as const
              ).map(([label, val, color]) => (
                <div key={label} style={{ borderLeft: '1px solid #30363d', paddingLeft: '1rem' }}>
                  <p className="sectionEyebrow" style={{ margin: '0 0 0.25rem' }}>{label}</p>
                  <p style={{ margin: 0, fontWeight: 800, fontSize: '1.4rem', color }}>{val}</p>
                </div>
              ))}
            </div>

            {/* View JSON / Export */}
            <div style={{ marginTop: '1rem', display: 'flex', gap: '0.75rem' }}>
              <button
                className="secondaryCta"
                style={{ fontSize: '0.8rem' }}
                onClick={() => {
                  const blob = new Blob([JSON.stringify(report, null, 2)], {
                    type: 'application/json',
                  });
                  const url = URL.createObjectURL(blob);
                  window.open(url, '_blank');
                }}
              >
                {'{ } View JSON'}
              </button>
              <button
                className="secondaryCta"
                style={{ fontSize: '0.8rem' }}
                onClick={() => {
                  const lines = [
                    `Production Readiness Report — ${formatTs(report.generated_at)}`,
                    '',
                    `Pilot Ready:       ${report.ready_for_pilot ? 'YES' : 'NO'}`,
                    `Paid Launch Ready: ${report.ready_for_paid_public_launch ? 'YES' : 'NO'}`,
                    '',
                    `Blocking Reasons (${report.blocking_reasons.length}):`,
                    ...report.blocking_reasons.map((r) => `  - ${humanize(r)}`),
                    '',
                    `Warnings (${report.warnings.length}):`,
                    ...report.warnings.map((w) => `  - ${humanize(w)}`),
                  ];
                  const blob = new Blob([lines.join('\n')], { type: 'text/plain' });
                  const url = URL.createObjectURL(blob);
                  const a = document.createElement('a');
                  a.href = url;
                  a.download = `readiness-report-${new Date().toISOString().slice(0, 10)}.txt`;
                  a.click();
                }}
              >
                ↓ Export Report
              </button>
            </div>
          </section>

          {/* ── Blockers & Warnings ──────────────────────────────────── */}
          {(report.blocking_reasons.length > 0 || report.warnings.length > 0) && (
            <section style={{ display: 'flex', gap: '1rem', marginBottom: '1rem' }}>
              <BlockerList
                title="Blocking Issues"
                items={report.blocking_reasons}
                variant="blocker"
              />
              <BlockerList
                title="Warnings"
                items={report.warnings}
                variant="warning"
              />
            </section>
          )}

          {/* ── Category Cards + Gate Criteria ──────────────────────── */}
          <div className="rdCategoryGrid" style={{ marginBottom: '1rem' }}>
            {CATEGORY_ORDER.map((name, i) => {
              const checks = report.categories[name] ?? [];
              // Span criteria card across remaining slot on last row if needed
              return <CategoryCard key={name} name={name} checks={checks} />;
            })}
            <ReadinessCriteria />
          </div>

          {/* ── Bottom note ─────────────────────────────────────────── */}
          <section
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '0.5rem',
              padding: '0.75rem 1rem',
              background: 'rgba(88,166,255,0.06)',
              border: '1px solid rgba(88,166,255,0.15)',
              borderRadius: '10px',
              marginBottom: '1rem',
            }}
          >
            <span style={{ fontSize: '0.9rem', color: '#58a6ff' }}>ℹ</span>
            <p style={{ margin: 0, fontSize: '0.82rem', color: '#8b949e' }}>
              Readiness data is collected from live systems and databases. No demo or mock data is
              used.
            </p>
          </section>
        </>
      )}
    </main>
  );
}
