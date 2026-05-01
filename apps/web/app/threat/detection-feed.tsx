import Link from 'next/link';

type DetectionItem = { id: string; title: string; severity: string; timestamp?: string | null; evidenceSummary?: string; state?: string };
type Props = { detections?: DetectionItem[]; loading?: boolean };

export default function DetectionFeed({ detections = [], loading = false }: Props) {
  return (
    <article className="dataCard" aria-label="Detection Feed">
      <div className="listHeader"><div><p className="sectionEyebrow">Detection feed</p><h3>Detection records from monitoring rules</h3></div><Link href="/alerts" prefetch={false}>Review alerts</Link></div>
      {loading ? <p className="muted">Loading detection records…</p> : null}
      {!loading && detections.length === 0 ? <p className="muted">No detections yet. Monitoring will show detections here once telemetry matches a rule.</p> : null}
      {detections.length > 0 ? <div className="stack compactStack">{detections.slice(0, 8).map((d) => <div className="overviewListItem" key={d.id}><div><p>{d.title}</p><p className="tableMeta">{d.severity} · {d.evidenceSummary || 'No evidence summary available.'}</p></div><span className="statusBadge statusBadge-low">{d.state || 'Open'}</span></div>)}</div> : null}
    </article>
  );
}
