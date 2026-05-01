import Link from 'next/link';

export default function ResponseActionsPage() {
  return (
    <main className="productPage">
      <section className="dataCard stack">
        <h1>Response Actions</h1>
        <p className="muted">Use alerts and incidents to triage and execute response actions for active threats.</p>
        <p><Link href="/alerts" prefetch={false}>Open alerts</Link></p>
        <p><Link href="/incidents" prefetch={false}>Open incidents</Link></p>
      </section>
    </main>
  );
}
