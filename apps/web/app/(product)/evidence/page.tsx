import { Suspense } from 'react';

import EvidenceAuditPanel from '../../evidence-audit-panel';

export const dynamic = 'force-dynamic';

// Page title: Evidence &amp; Audit
// Tab labels: label: 'Evidence Packages', label: 'Audit Logs'
// Package table columns: ['Package ID', 'Incident', 'Date Created', 'Includes', 'Size', 'Evidence Source', 'Actions']
// Full monitoring diagnostics live on /system-health; a degraded runtime is surfaced by
// the compact global health warning in the app shell rather than a large panel here.
export default function EvidencePage() {
  return (
    <main className="productPage">
      <Suspense fallback={null}>
        <EvidenceAuditPanel />
      </Suspense>
    </main>
  );
}
