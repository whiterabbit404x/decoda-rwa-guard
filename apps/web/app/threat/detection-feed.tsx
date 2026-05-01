import type { ReactNode } from 'react';

type Row = { time: string; asset: string; detection: string; severity: string; confidence: string; evidence: string; status: string };

type Props = { rows?: Row[]; loading?: boolean; actions?: ReactNode; children?: ReactNode };

export default function DetectionFeed({ rows, loading, actions, children }: Props) {
  if (children) return <section aria-label="Detection Feed">{children}</section>;
  const safeRows = rows ?? [];
  return (
    <article className="dataCard" aria-label="Detection Feed">
      <div className="listHeader"><div><p className="sectionEyebrow">Detection feed</p><h3>Detection records from monitoring rules</h3></div>{actions}</div>
      {loading ? <p className="muted">Loading detection records…</p> : null}
      {!loading && safeRows.length === 0 ? <p className="muted">No detections yet. Monitoring will show detections here once telemetry matches a rule.</p> : null}
      {safeRows.length > 0 ? <div className="tableWrap"><table><thead><tr><th>Time</th><th>Asset</th><th>Detection</th><th>Severity</th><th>Confidence</th><th>Evidence</th><th>Status</th></tr></thead><tbody>{safeRows.map((r, i) => <tr key={`${r.time}-${i}`}><td>{r.time}</td><td>{r.asset}</td><td>{r.detection}</td><td>{r.severity}</td><td>{r.confidence}</td><td>{r.evidence}</td><td>{r.status}</td></tr>)}</tbody></table></div> : null}
    </article>
  );
}
