import type { DetectionRecord } from './detection-feed';

type DetectionInput = {
  id: string;
  timestamp: string;
  assetName: string;
  title: string;
  ruleLabel?: string;
  severity: string;
  monitoringStatus: string;
  evidenceSummary: string;
  state: string;
};

export function buildDetectionRecords(detections: DetectionInput[]): DetectionRecord[] {
  const formatAbsoluteTime = (value?: string | null): string => {
    if (!value) return 'Not available';
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? 'Not available' : date.toLocaleString();
  };

  return detections.map((item) => ({
    id: item.id,
    time: formatAbsoluteTime(item.timestamp),
    asset: item.assetName,
    detection: item.title,
    ruleLabel: item.ruleLabel,
    severity: item.severity,
    confidence: item.monitoringStatus,
    evidence: item.evidenceSummary,
    status: item.state,
  }));
}
