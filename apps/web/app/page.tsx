type DashboardCard = {
  title: string;
  status: string;
  detail: string;
  service: string;
};

type ServiceStatus = {
  service_name: string;
  port: number;
  status: string;
  detail: string;
  updated_at: string;
};

type DashboardResponse = {
  mode: string;
  database_url: string;
  redis_enabled: boolean;
  cards: DashboardCard[];
  services: ServiceStatus[];
};

const fallbackCards: DashboardCard[] = [
  {
    title: 'API Gateway',
    status: 'Waiting',
    detail: 'Start the local backend to populate the live dashboard.',
    service: 'api'
  }
];

async function getDashboard(): Promise<DashboardResponse | null> {
  const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

  try {
    const response = await fetch(`${apiUrl}/dashboard`, { cache: 'no-store' });

    if (!response.ok) {
      return null;
    }

    return (await response.json()) as DashboardResponse;
  } catch {
    return null;
  }
}

export default async function Page() {
  const dashboard = await getDashboard();
  const cards = dashboard?.cards?.length ? dashboard.cards : fallbackCards;
  const services = dashboard?.services ?? [];

  return (
    <main className="container">
      <div className="hero">
        <div>
          <p className="eyebrow">Phase 1 local development</p>
          <h1>Tokenized Treasury Control Dashboard</h1>
          <p className="lede">
            Run the Next.js frontend and the FastAPI backend locally with SQLite-backed sample data and no Docker requirement.
          </p>
        </div>
        <div className="heroPanel">
          <p><strong>Mode:</strong> {dashboard?.mode ?? 'local'}</p>
          <p><strong>Database:</strong> {dashboard?.database_url ?? 'sqlite:///.data/phase1.db'}</p>
          <p><strong>Redis:</strong> {dashboard?.redis_enabled ? 'enabled' : 'disabled for local mode'}</p>
        </div>
      </div>

      <section className="grid">
        {cards.map((card) => (
          <article key={`${card.service}-${card.title}`} className="card">
            <p className="serviceTag">{card.service}</p>
            <h2>{card.title}</h2>
            <p className="status">Status: {card.status}</p>
            <p>{card.detail}</p>
          </article>
        ))}
      </section>

      <section className="serviceSection">
        <div className="sectionHeader">
          <h2>Backend services</h2>
          <p>Each service can run locally with Uvicorn and the shared SQLite file.</p>
        </div>
        <div className="serviceList">
          {services.length > 0 ? (
            services.map((service) => (
              <article key={service.service_name} className="serviceCard">
                <div className="serviceCardHeader">
                  <h3>{service.service_name}</h3>
                  <span className="pill">:{service.port}</span>
                </div>
                <p className="status">Status: {service.status}</p>
                <p>{service.detail}</p>
                <p className="timestamp">Updated {new Date(service.updated_at).toLocaleString()}</p>
              </article>
            ))
          ) : (
            <article className="serviceCard emptyState">
              <h3>Backend not running yet</h3>
              <p>Run <code>make init-local</code> and <code>make run-backend</code> to view live service status here.</p>
            </article>
          )}
        </div>
      </section>
    </main>
  );
}
