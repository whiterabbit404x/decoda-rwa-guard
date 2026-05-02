import type { DetectionRecord } from './detection-feed';

type DetectionInput = {
  id: string;
  timestamp: string;
  assetName: string;
  title: string;
  severity: string;
  monitoringStatus: string;
  evidenceSummary: string;
  state: string;
};

export function buildDetectionRecords(detections: DetectionInput[]): DetectionRecord[] {
  return detections.map((item) => ({
    id: item.id,
    time: item.timestamp,
    asset: item.assetName,
    detection: item.title,
    severity: item.severity,
    confidence: item.monitoringStatus,
    evidence: item.evidenceSummary,
    status: item.state,
  }));
}
