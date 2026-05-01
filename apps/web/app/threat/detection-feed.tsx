import ThreatEmptyState from './threat-empty-state';

export type DetectionFeedRow = {
  id: string;
  timeLabel: string;
  assetLabel: string;
  detectionLabel: string;
  severityLabel: string;
  confidenceLabel: string;
  evidenceLabel: string;
  statusLabel: string;
};

import type { ReactNode } from 'react';

type Props = {
  rows?: DetectionFeedRow[];
  children?: ReactNode;
};

export default function DetectionFeed({ rows = [], children }: Props) {
  if (rows.length === 0) {
    return children ? <section aria-label="Detection Feed">{children}</section> : <ThreatEmptyState title="No detections yet" message="Detections will appear here once monitoring captures customer-safe evidence." />;
  }

  return (
    <section aria-label="Detection Feed" className="sidebarMetaCard">
      <h3>Detection feed</h3>
      <table>
        <thead>
          <tr>
            <th>Time</th><th>Asset</th><th>Detection</th><th>Severity</th><th>Confidence</th><th>Evidence</th><th>Status</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id}>
              <td>{row.timeLabel}</td><td>{row.assetLabel}</td><td>{row.detectionLabel}</td><td>{row.severityLabel}</td><td>{row.confidenceLabel}</td><td>{row.evidenceLabel}</td><td>{row.statusLabel}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {children}
    </section>
  );
}
