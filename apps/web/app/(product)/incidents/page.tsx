import IncidentsPanel from '../../incidents-panel';

export const dynamic = 'force-dynamic';

// Infrastructure diagnostics live on /system-health; this page prioritizes Incidents.
// A degraded runtime is surfaced by the header's monitoring status in the app shell.
// The panel renders the page header itself, so Create Incident sits beside the title.
export default function IncidentsPage() {
  return (
    <main className="productPage">
      <IncidentsPanel />
    </main>
  );
}
