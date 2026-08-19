import { type ReliabilityMetrics } from './reliability-types';

type Props = {
  metrics: ReliabilityMetrics;
};

function trendGlyph(trend: string | null | undefined): { glyph: string; className: string } | null {
  if (trend === 'improving') return { glyph: '↑', className: 'shRelTrend shRelTrendUp' };
  if (trend === 'worsening') return { glyph: '↓', className: 'shRelTrend shRelTrendDown' };
  if (trend === 'unchanged') return { glyph: '→', className: 'shRelTrend' };
  return null; // no arrow without comparison data
}

// A metric card that renders a real value OR a truthful absence label — never a
// fabricated number. `state` drives which is shown.
function MetricCard({
  title,
  windowLabel,
  value,
  emptyLabel,
  source,
  trend,
}: {
  title: string;
  windowLabel: string | null | undefined;
  value: string | null;
  emptyLabel: string;
  source: string | null | undefined;
  trend?: string | null;
}) {
  const t = value != null ? trendGlyph(trend) : null;
  return (
    <article className="dataCard shSummaryCard">
      <div className="shSummaryCardHeader">
        <p className="shSummaryCardTitle">{title}</p>
        {windowLabel ? <span className="shRelWindow">{windowLabel}</span> : null}
      </div>
      {value != null ? (
        <p className="shRelMetricValue">
          {value}
          {t ? <span className={t.className} aria-hidden="true">{t.glyph}</span> : null}
        </p>
      ) : (
        <p className="shRelMetricEmpty">{emptyLabel}</p>
      )}
      {source ? <p className="shSummaryTime" title={source}>Source: {source}</p> : null}
    </article>
  );
}

function uptimeValue(m: ReliabilityMetrics['uptime']): string | null {
  if (m.state === 'ok' && m.value != null) return `${m.value.toFixed(2)}%`;
  return null;
}

function uptimeEmpty(m: ReliabilityMetrics['uptime']): string {
  if (m.state === 'insufficient_history') return 'Insufficient history';
  if (m.state === 'unavailable') return 'Unavailable';
  return 'No data';
}

function responseValue(m: ReliabilityMetrics['response_time']): string | null {
  if (m.value_ms != null && (m.state === 'ok' || m.state === 'point_sample')) return `${Math.round(m.value_ms)} ms`;
  return null;
}

function errorValue(m: ReliabilityMetrics['error_rate']): string | null {
  if (m.state === 'ok' && m.value != null) return `${m.value.toFixed(2)}%`;
  return null;
}

export function ReliabilitySummaryCards({ metrics }: Props) {
  const responseWindow =
    metrics.response_time.state === 'point_sample'
      ? 'now'
      : metrics.response_time.window_label ?? null;
  return (
    <div className="shRelSummaryGrid">
      <MetricCard
        title="Uptime"
        windowLabel={metrics.uptime.window_label ?? '30d'}
        value={uptimeValue(metrics.uptime)}
        emptyLabel={uptimeEmpty(metrics.uptime)}
        source={metrics.uptime.source ?? null}
      />
      <MetricCard
        title="Avg Response Time"
        windowLabel={responseWindow}
        value={responseValue(metrics.response_time)}
        emptyLabel="No data"
        source={metrics.response_time.source ?? null}
        trend={metrics.response_time.trend}
      />
      <MetricCard
        title="Error Rate"
        windowLabel={metrics.error_rate.window_label ?? '24h'}
        value={errorValue(metrics.error_rate)}
        emptyLabel={metrics.error_rate.state === 'unavailable' ? 'Unavailable' : 'No data'}
        source={metrics.error_rate.source ?? null}
        trend={metrics.error_rate.trend}
      />
    </div>
  );
}
