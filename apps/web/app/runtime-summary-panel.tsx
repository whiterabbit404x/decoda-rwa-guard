'use client';

import { useRuntimeSummary } from './runtime-summary-context';

export default function RuntimeSummaryPanel() {
  const { summary, existsLabel, missingLabel, nextActionLabel, evidenceLabel } = useRuntimeSummary();
  return (
    <article className="dataCard">
      <p className="sectionEyebrow">Runtime summary</p>
      <p className="muted"><strong>What exists:</strong> {existsLabel}</p>
      <p className="muted"><strong>What is missing:</strong> {missingLabel}</p>
      <p className="muted"><strong>Next required action:</strong> {nextActionLabel}</p>
      <p className="muted">Evidence: {evidenceLabel}</p>
      <p className="muted">Status: <span className="ruleChip">{summary.runtime_status}</span> <span className="ruleChip">{summary.monitoring_status}</span></p>
    </article>
  );
}
