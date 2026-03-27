import Link from 'next/link';

export const dynamic = 'force-dynamic';

export default function HelpPage() {
  return (
    <main className="productPage">
      <section className="featureSection">
        <div className="sectionHeader">
          <div>
            <p className="eyebrow">Help center</p>
            <h1>Start here: self-serve workspace onboarding</h1>
            <p className="lede">Everything teams need to configure and operate Decoda RWA Guard independently.</p>
          </div>
        </div>
        <div className="threeColumnSection">
          <article className="dataCard"><h2>1) Create workspace</h2><p className="muted">Create/select your workspace and confirm billing plan.</p><Link href="/workspaces">Open workspaces</Link></article>
          <article className="dataCard"><h2>2) Connect integrations</h2><p className="muted">Configure Slack/webhooks, then run test notifications.</p><Link href="/integrations">Open integrations</Link></article>
          <article className="dataCard"><h2>3) Run operations</h2><p className="muted">Add targets/assets, run analyses, and manage findings.</p><Link href="/dashboard">Open dashboard</Link></article>
        </div>
      </section>
    </main>
  );
}
