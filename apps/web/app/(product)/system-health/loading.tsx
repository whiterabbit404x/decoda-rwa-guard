export default function SystemHealthLoading() {
  return (
    <main className="productPage">
      <section className="hero compactHero">
        <div>
          <p className="eyebrow">Status &amp; operations</p>
          <h1>System Health</h1>
          <p className="lede">Loading system health…</p>
        </div>
      </section>

      <section className="dataCard shHero" aria-busy="true">
        <div className="shHeroTop">
          <div
            className="healthIcon healthIconDegraded"
            style={{ width: '3.5rem', height: '3.5rem', fontSize: '1.5rem', flexShrink: 0 }}
          >
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
