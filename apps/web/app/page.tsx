const cards = [
  { title: "Portfolio Risk", status: "Nominal", detail: "VaR and stress controls placeholder." },
  { title: "Compliance Monitor", status: "Passing", detail: "Policy checks and alerts placeholder." },
  { title: "Oracle Data Feed", status: "Live", detail: "Market data freshness placeholder." },
  { title: "Reconciliation", status: "In Sync", detail: "Cash/token ledger parity placeholder." }
];

export default function Page() {
  return (
    <main className="container">
      <h1>Phase 1 Tokenized Treasury Dashboard</h1>
      <p>Foundational visibility panels for risk-control workflows.</p>
      <section className="grid">
        {cards.map((card) => (
          <article key={card.title} className="card">
            <h2>{card.title}</h2>
            <p className="status">Status: {card.status}</p>
            <p>{card.detail}</p>
          </article>
        ))}
      </section>
    </main>
  );
}
