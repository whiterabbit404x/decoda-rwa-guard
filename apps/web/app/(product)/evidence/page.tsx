import { Suspense } from 'react';

import EvidenceAuditPanel from '../../evidence-audit-panel';
import RuntimeSummaryPanel from '../../runtime-summary-panel';

export const dynamic = 'force-dynamic';

// Page title: Evidence &amp; Audit
// Tab labels: label: 'Evidence Packages', label: 'Audit Logs'
// Package table columns: ['Package ID', 'Incident', 'Date Created', 'Includes', 'Size', 'Evidence Source', 'Actions']
export default function EvidencePage() {
  return (
    <main className="productPage">
      <RuntimeSummaryPanel />
      <Suspense fallback={null}>
        <EvidenceAuditPanel />
      </Suspense>
    </main>
  );
}
