import IncidentsPanel from '../../incidents-panel';

export const dynamic = 'force-dynamic';

// Infrastructure diagnostics live on /system-health; this page prioritizes Incidents.
// A degraded runtime is surfaced by the compact global health warning in the app shell.
export default function IncidentsPage() {
  return (
    <main className="productPage">
      <section className="hero compactHero">
        <div>
          <p className="eyebrow">Investigation Workflow</p>
          <h1>Incidents</h1>
          <p className="lede">
            Investigate alert-driven incidents, evidence, and response progress.
          </p>
        </div>
      </section>
      <IncidentsPanel />
    </main>
  );
}
