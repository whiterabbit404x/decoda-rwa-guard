import Link from 'next/link';
import { THREAT_COPY } from './threat-copy';

export type DetectionRecord = {
  id: string;
  time: string;
  asset: string;
  detection: string;
  ruleLabel?: string;
  severity: string;
  confidence: string;
  evidence: string;
  status: string;
};

type Props = { detections: DetectionRecord[]; loading: boolean };

function SeverityBadge({ severity }: { severity: string }) {
  const s = severity.toLowerCase();
  const style: React.CSSProperties =
    s === 'critical' || s === 'high'
      ? { background: 'var(--danger-bg)', color: 'var(--danger-fg)', border: '1px solid var(--danger-bdr)' }
      : s === 'medium'
        ? { background: 'var(--warning-bg)', color: 'var(--warning-fg)', border: '1px solid var(--warning-bdr)' }
        : { background: 'var(--success-bg)', color: 'var(--success-fg)', border: '1px solid var(--success-bdr)' };

  return (
    <span style={{ display: 'inline-block', fontSize: '0.75rem', fontWeight: 700, padding: '0.15rem 0.5rem', borderRadius: '999px', textTransform: 'uppercase', ...style }}>
      {severity}
    </span>
  );
}

export default function DetectionFeed({ detections, loading }: Props) {
  return (
    <article className="dataCard" aria-label="Detection Feed">
      <div className="listHeader">
        <div>
          <p className="sectionEyebrow">Detection feed</p>
          <h3 style={{ fontSize: '1.1rem', fontWeight: 700, margin: '0.15rem 0 0' }}>Detection records from monitoring rules</h3>
        </div>
        <Link href="/alerts" prefetch={false} className="secondaryCta">Review alerts</Link>
      </div>
      {loading ? (
        <p className="muted" style={{ padding: '2rem 0', textAlign: 'center', fontSize: '0.9rem' }}>Loading detection records…</p>
      ) : null}
      {!loading && detections.length === 0 ? (
        <div style={{ padding: '3rem 1rem', textAlign: 'center' }}>
          <p style={{ fontSize: '1rem', color: 'var(--text-secondary)', margin: '0 0 0.75rem', fontWeight: 600 }}>
            No detections yet
          </p>
          <p className="muted" style={{ fontSize: '0.9375rem', maxWidth: '42rem', margin: '0 auto 1rem' }}>
            {THREAT_COPY.noDetectionRecords}
          </p>
          <Link href="/monitoring-sources" prefetch={false} className="secondaryCta">
            Review monitoring coverage
          </Link>
        </div>
      ) : null}
      {detections.length > 0 ? (
        <div className="tableWrap">
          <table>
            <thead>
              <tr>
                <th>Time</th>
                <th>Asset</th>
                <th>Detection</th>
                <th>Rule</th>
                <th>Severity</th>
                <th>Confidence</th>
                <th>Evidence</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {detections.slice(0, 8).map((d) => (
                <tr key={d.id}>
                  <td style={{ fontSize: '0.875rem', whiteSpace: 'nowrap' }}>{d.time}</td>
                  <td style={{ fontSize: '0.875rem' }}>{d.asset}</td>
                  <td style={{ fontSize: '0.875rem' }}>{d.detection}</td>
                  <td style={{ fontSize: '0.875rem' }}>{d.ruleLabel || 'n/a'}</td>
                  <td><SeverityBadge severity={d.severity} /></td>
                  <td style={{ fontSize: '0.875rem' }}>{d.confidence}</td>
                  <td style={{ fontSize: '0.875rem' }}>{d.evidence}</td>
                  <td style={{ fontSize: '0.875rem' }}>{d.status}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </article>
  );
}
