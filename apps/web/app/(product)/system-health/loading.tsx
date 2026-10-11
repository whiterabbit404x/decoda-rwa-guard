export default function SystemHealthLoading() {
  return (
    <main className="productPage shPage">
      <section className="hero compactHero">
        <div>
          <p className="eyebrow">Status &amp; operations</p>
          <h1>System Health</h1>
          <p className="lede">Loading system health…</p>
        </div>
      </section>

      <section className="dataCard shHero" aria-busy="true">
        <div className="shHeroTop">
          {/* Neutral while loading: no status is known yet. */}
          <div className="healthIcon shHeroIcon shHeroIconPending" aria-hidden="true">
            …
          </div>
          <div className="shHeroMain">
            <p className="shHeroSummary">Loading system health…</p>
            <p className="explanation small">Contacting the system-health API and reading live component status.</p>
          </div>
        </div>
      </section>
    </main>
  );
}
