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

export default function DetectionFeed({ detections, loading }: Props) {
  return (
    <article className="dataCard" aria-label="Detection Feed">
      <div className="listHeader"><div><p className="sectionEyebrow">Detection feed</p><h3>Detection records from monitoring rules</h3></div><Link href="/alerts" prefetch={false}>Review alerts</Link></div>
      {loading ? <p className="muted">Loading detection records…</p> : null}
      {!loading && detections.length === 0 ? <p className="muted">{THREAT_COPY.noDetectionRecords}</p> : null}
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
                  <td>{d.time}</td>
                  <td>{d.asset}</td>
                  <td>{d.detection}</td>
                  <td>{d.ruleLabel || 'n/a'}</td>
                  <td>{d.severity}</td>
                  <td>{d.confidence}</td>
                  <td>{d.evidence}</td>
                  <td>{d.status}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </article>
  );
}
