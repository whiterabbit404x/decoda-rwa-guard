import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

// Source-inspection specs (matching the repo's existing response-actions specs).
// They lock in the wiring that makes accepted/rejected/pending AI recommendation
// reviews visible on the Response Actions page as immutable, never-executed records.

function appSource(fileName: string): string {
  return fs.readFileSync(path.join(__dirname, '..', 'app', fileName), 'utf-8');
}

function pageSource(): string {
  return appSource('(product)/response-actions-page-client.tsx');
}

function auditPanelSource(): string {
  return appSource('evidence-audit-panel.tsx');
}

// 1 & 2. Accepted and rejected AI reviews are routed into Action History.
test('accepted/rejected AI reviews are routed to Action History via decidedAiReviews', () => {
  const src = pageSource();
  expect(src).toContain('normalizeAiReviewHistoryRow');
  expect(src).toContain('decidedAiReviews');
  // Decided = accepted OR rejected.
  expect(src).toContain("String(item?.review_state || '') === 'accepted'");
  expect(src).toContain("String(item?.review_state || '') === 'rejected'");
  // Decided reviews are prepended to the history rows.
  expect(src).toContain('decidedAiReviews.map(normalizeAiReviewHistoryRow)');
});

// 3. Pending AI reviews are routed into Recommended Actions (with legacy actions).
test('pending AI reviews are routed to Recommended Actions', () => {
  const src = pageSource();
  expect(src).toContain('pendingAiReviews');
  expect(src).toContain("String(item?.review_state || 'pending_review') === 'pending_review'");
  expect(src).toContain('[...legacyActions, ...pendingAiReviews].map');
});

// 4. Source shows AI Investigation (table + truthful evidence pill, never simulator/live).
test('AI review rows show AI Investigation source and an AI evidence pill', () => {
  const src = pageSource();
  expect(src).toContain("'AI Investigation'");
  expect(src).toContain("raw === 'ai_investigation'");
  expect(src).toContain("{ label: 'AI investigation', variant: 'info' }");
});

// 5. Accepted record shows Executed: No, and is normalized as not executed.
test('AI review records are shown as not executed', () => {
  const src = pageSource();
  // Executed column renders "No" for AI review rows.
  expect(src).toContain('<StatusPill label="No" variant="neutral" />');
  // Normalization hard-codes executed=false for review records.
  expect(src).toContain('executed: false');
  // A "Not executed" badge is shown in the detail panel.
  expect(src).toContain('Not executed');
});

// 6. No Simulate Action button on an AI recommendation-review record.
test('AI review records never expose Simulate/Execute controls', () => {
  const src = pageSource();
  // canExecute is gated off for AI reviews.
  expect(src).toContain('!isAiReview');
  expect(src).toContain("const isAiReview = action.recordType === 'ai_recommendation_review'");
  // The AI branch offers neutral read links instead of Simulate/Execute.
  expect(src).toContain('View investigation');
  expect(src).toContain('View recommendation');
  expect(src).toContain('View evidence');
});

// 7. Legacy simulator action keeps its simulation controls (unchanged behavior).
test('legacy actions still have Simulate Action controls', () => {
  const src = pageSource();
  expect(src).toContain('Simulate Action');
  expect(src).toContain('void simulateAction()');
  // Simulate button lives in the non-AI branch.
  expect(src).toContain('/api/response/actions/${action.id}/simulate');
});

// 8. Incident filtering flows through to the API list request.
test('incident_id filter is forwarded to the response actions API', () => {
  const src = pageSource();
  expect(src).toContain("const incidentIdFilter = searchParams.get('incident_id')");
  expect(src).toContain("actionsQsParams.set('incident_id', incidentIdFilter)");
});

// 9 & 10. View Incident / View Evidence links resolve to the linked incident.
test('AI review history rows link to the incident and its evidence', () => {
  const src = pageSource();
  expect(src).toContain('View Incident');
  expect(src).toContain('View Evidence');
  expect(src).toContain('href={`/incidents/${row.linkedIncident}`}');
  expect(src).toContain('href={`/evidence?incident_id=${row.linkedIncident}`}');
});

// 11. Empty-state blocker only appears when there are truly zero matching records
//     (decided AI reviews live only in history, so history presence must count).
test('empty-state blocker respects history rows, not just recommended rows', () => {
  const src = pageSource();
  expect(src).toContain('if (recommendedRows.length > 0 || historyRows.length > 0) return null;');
  // The "no matching records" filter message is still present for genuinely empty filters.
  expect(src).toContain('No actions match current filters');
});

// 12. Existing tabs and filters remain functional (regression guard).
test('tabs and filters remain intact', () => {
  const src = pageSource();
  expect(src).toContain("{ key: 'recommended', label: 'Recommended Actions' }");
  expect(src).toContain("{ key: 'history', label: 'Action History' }");
  expect(src).toContain('Search actions...');
  expect(src).toContain('Type filter');
  expect(src).toContain('Status filter');
  expect(src).toContain('Approval filter');
});

// AI recommendation badges are rendered (AI recommendation / decision / Not executed).
test('AI recommendation badges are rendered', () => {
  const src = pageSource();
  expect(src).toContain('<StatusPill label="AI recommendation" variant="info" />');
  expect(src).toContain('<StatusPill label="Accepted" variant="success" />');
  expect(src).toContain('<StatusPill label="Rejected" variant="neutral" />');
});

// Evidence & Audit no longer renders recommendation decisions as "Unknown source".
test('evidence & audit panel maps AI investigation sources truthfully', () => {
  const src = auditPanelSource();
  expect(src).toContain("raw === 'ai_investigation'");
  expect(src).toContain("{ label: 'AI investigation', variant: 'info' }");
  expect(src).toContain("raw === 'human_recommendation_review'");
  // The "Unknown source" fallback still exists for genuinely unlabeled rows.
  expect(src).toContain("{ label: 'Unknown source', variant: 'neutral' }");
});
