import EvidenceAuditPanel from '../../evidence-audit-panel';
import RuntimeSummaryPanel from '../../runtime-summary-panel';

export const dynamic = 'force-dynamic';

export default function EvidencePage() {
  return (
    <main className="productPage">
      <RuntimeSummaryPanel />
      <EvidenceAuditPanel />
    </main>
  );
}
