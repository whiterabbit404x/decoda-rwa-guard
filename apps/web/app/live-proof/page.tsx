import Link from 'next/link';
import { readFileSync } from 'fs';
import { join } from 'path';
import { getBuildInfo } from '../build-info';

export const dynamic = 'force-dynamic';

// ─── Network naming ───────────────────────────────────────────
// Decoda's live monitoring environment runs on Base Mainnet (chain 8453)
// today. This map is only a chain-id → human-name formatter for whatever chain
// a CI proof artifact actually probed — it is never a claim that a network is
// supported. The observed chain id is read from the artifact, never hard-coded.
const CHAIN_NAMES: Record<string, string> = {
  '1': 'Ethereum Mainnet',
  '8453': 'Base Mainnet',
  '42161': 'Arbitrum One',
};

function chainLabel(chainId: string | undefined): string {
  if (!chainId) return '—';
  const id = String(chainId).trim();
  const name = CHAIN_NAMES[id];
  return name ? `${id} (${name})` : `${id}`;
}

// ─── Safe artifact reader ─────────────────────────────────────
// Reads a proof artifact if it happens to be attached to this deployment.
// The `latest/` proof aliases are intentionally gitignored (diagnostic-only,
// per repo policy they must never become release claims), so on a public
// deployment these reads normally return null — which the page treats as
// "not published", never as success.

function readArtifact(relPath: string): Record<string, unknown> | null {
  const candidates = [
    join(process.cwd(), '..', '..', 'artifacts', relPath),
    join(process.cwd(), 'artifacts', relPath),
    join(process.cwd(), '..', 'artifacts', relPath),
  ];
  for (const abs of candidates) {
    try {
      const raw = readFileSync(abs, 'utf-8');
      return JSON.parse(raw) as Record<string, unknown>;
    } catch {
      continue;
    }
  }
  return null;
}

// ─── Types ───────────────────────────────────────────────────

type ProofStatus = 'pass' | 'fail' | 'unknown';

interface ProofCard {
  title: string;
  status: ProofStatus;
  statusLabel: string;
  lines: Array<{ label: string; value: string }>;
  note?: string;
}

// ─── Genuine, always-available deployment evidence ───────────
// Read live from the running deployment serving this request — not from a
// stored artifact. This is the one card that always reflects real state.

function buildDeploymentCard(): ProofCard {
  const build = getBuildInfo(process.env);
  const configured = build.runtimeConfig.configured;
  return {
    title: 'Build & Deployment',
    status: configured ? 'pass' : 'unknown',
    statusLabel: configured ? 'Live deployment' : 'Backend not configured',
    lines: [
      { label: 'Environment', value: build.vercelEnv ?? '—' },
      { label: 'Commit', value: build.shortCommitSha ?? 'unknown' },
      { label: 'Built at', value: build.buildTimestamp ? build.buildTimestamp.replace('T', ' ').replace(/\.\d+.*$/, ' UTC') : '—' },
      { label: 'Backend API configured', value: configured ? 'Yes' : 'No' },
      { label: 'Live monitoring mode', value: build.runtimeConfig.liveModeEnabled ? 'Enabled' : 'Disabled' },
    ],
    note: 'Read live from the deployment serving this request — not from a stored artifact.',
  };
}

// ─── Diagnostic proof cards from CI artifacts (if attached) ──
// These are produced by internal CI / staging live-evidence pipelines. When an
// artifact is not attached to this deployment the card comes back with status
// `unknown` and is simply not rendered — missing proof is never shown as, or
// counted toward, successful proof.

function buildDiagnosticCards(): ProofCard[] {
  const readiness = readArtifact('final-readiness/latest/summary.json');
  const liveEvidence = readArtifact('live-evidence-proof/latest/summary.json');
  const sellNow = readArtifact('sell-now-proof/latest/summary.json');
  const releaseProof = readArtifact('release-proof/latest/summary.json');

  const cards: ProofCard[] = [];

  // 1. Overall readiness
  {
    const score = readiness?.overall_score as number | undefined;
    const ready100 = readiness?.production_100_percent_ready as boolean | undefined;
    const generatedAt = readiness?.generated_at as string | undefined;
    if (readiness) {
      cards.push({
        title: 'Production Readiness Gates',
        status: ready100 ? 'pass' : 'fail',
        statusLabel: ready100 ? 'All gates passing' : score !== undefined ? `Score: ${score}/100` : 'Incomplete',
        lines: [
          { label: 'Overall score', value: score !== undefined ? `${score}/100` : '—' },
          { label: 'Controlled pilot ready', value: readiness?.controlled_pilot_ready ? 'Yes' : '—' },
          { label: 'Broad paid SaaS ready', value: readiness?.broad_paid_saas_ready ? 'Yes' : '—' },
          { label: 'Enterprise procurement ready', value: readiness?.enterprise_procurement_ready ? 'Yes' : '—' },
          { label: 'Generated at', value: generatedAt ? generatedAt.replace('T', ' ').replace(/\.\d+.*$/, ' UTC') : '—' },
        ],
      });
    }
  }

  // 2. Live provider telemetry
  {
    const lep = liveEvidence?.live_provider_evidence as Record<string, unknown> | undefined;
    const ready = lep?.live_evidence_ready as boolean | undefined;
    const block = lep?.block_number_observed as string | undefined;
    const chainId = lep?.chain_id_observed as string | undefined;
    const providerMode = lep?.provider_mode as string | undefined;
    const generatedAt = liveEvidence?.generated_at as string | undefined;
    const githubRunId = lep?.github_run_id as string | undefined;
    if (lep) {
      cards.push({
        title: 'Live Provider Telemetry',
        status: ready ? 'pass' : 'fail',
        statusLabel: ready ? 'Proven in CI' : 'Proof incomplete',
        lines: [
          { label: 'Provider mode', value: providerMode ?? '—' },
          { label: 'Chain observed', value: chainLabel(chainId) },
          { label: 'Block observed', value: block ? `#${block}` : '—' },
          { label: 'CI run', value: githubRunId ? `#${githubRunId}` : '—' },
          { label: 'Proof generated', value: generatedAt ? generatedAt.replace('T', ' ').replace(/\.\d+.*$/, ' UTC') : '—' },
        ],
      });
    }
  }

  // 3. Evidence chain
  {
    const lep = liveEvidence?.live_provider_evidence as Record<string, unknown> | undefined;
    const chain = lep?.chain as Record<string, unknown> | undefined;
    const ready = lep?.live_evidence_ready as boolean | undefined;
    if (chain) {
      cards.push({
        title: 'Alert → Incident → Evidence Chain',
        status: ready ? 'pass' : 'fail',
        statusLabel: ready ? 'Proven end-to-end' : 'Partial',
        lines: [
          { label: 'Telemetry event ID', value: chain?.telemetry_event_id as string ?? '—' },
          { label: 'Detection ID', value: chain?.detection_id as string ?? '—' },
          { label: 'Alert ID', value: chain?.alert_id as string ?? '—' },
          { label: 'Incident ID', value: chain?.incident_id as string ?? '—' },
          { label: 'Evidence package ID', value: chain?.evidence_package_id as string ?? '—' },
        ],
        note: ready
          ? 'All IDs are live — generated from real on-chain telemetry, not seeded or simulated data.'
          : undefined,
      });
    }
  }

  // 4. CI release gates
  {
    const ciReady = sellNow?.release_ci_gates_ready as boolean | undefined;
    const testReady = sellNow?.release_test_report_ready as boolean | undefined;
    const releaseStatus = releaseProof?.release_status as string | undefined ?? sellNow?.release_status as string | undefined;
    const generatedAt = releaseProof?.generated_at as string | undefined;
    if (sellNow || releaseProof) {
      cards.push({
        title: 'CI Release Gates',
        status: ciReady && testReady ? 'pass' : 'fail',
        statusLabel: ciReady && testReady ? 'All gates green' : 'Gates incomplete',
        lines: [
          { label: 'Release status', value: releaseStatus ?? '—' },
          { label: 'CI gates', value: ciReady ? 'Passing' : ciReady === false ? 'Failing' : '—' },
          { label: 'Test report', value: testReady ? 'Present' : testReady === false ? 'Missing' : '—' },
          { label: 'GitHub Actions visible green', value: sellNow?.github_actions_visible_green ? 'Yes' : '—' },
          { label: 'Proof generated', value: generatedAt ? generatedAt.replace('T', ' ').replace(/\.\d+.*$/, ' UTC') : '—' },
        ],
      });
    }
  }

  return cards;
}

// ─── Page ─────────────────────────────────────────────────────

export default function LiveProofPage() {
  const deploymentCard = buildDeploymentCard();
  // Only surface diagnostic proof cards whose artifact is actually attached to
  // this deployment. Absent artifacts are not shown as broken or pending — they
  // are simply not published yet.
  const publishedCards = buildDiagnosticCards().filter((c) => c.status !== 'unknown');
  const hasPublished = publishedCards.length > 0;
  const publishedFail = publishedCards.filter((c) => c.status === 'fail').length;
  const allPublishedPass = hasPublished && publishedFail === 0;

  return (
    <main className="proofPage">
      <nav className="trustNav" aria-label="Breadcrumb">
        <Link href="/" className="trustNavBack" prefetch={false}>← Back to Decoda RWA Guard</Link>
      </nav>

      {/* ── Hero ─────────────────────────────────────────────── */}
      <header className="proofHero">
        <p className="mktSectionLabel">LIVE PROOF</p>
        <h1 className="proofHeroTitle">
          Live deployment status — read from the running system, never fabricated.
        </h1>
        <p className="proofHeroSubtitle">
          The deployment card below is read live from the running Decoda RWA Guard deployment serving this request.
          Decoda also runs internal CI and staging live-evidence proof pipelines; when a proof run is attached to a
          public deployment, its results appear here. Nothing on this page is simulated, seeded, or hard-coded — when a
          proof artifact is not published to this deployment, it is not shown.
        </p>
        {hasPublished ? (
          <div className={`proofSummaryBadge${allPublishedPass ? ' proofSummaryBadge--pass' : ' proofSummaryBadge--fail'}`}>
            {allPublishedPass
              ? '✓ All published proof checks passing'
              : `✗ ${publishedFail} published proof check${publishedFail > 1 ? 's' : ''} need attention`}
          </div>
        ) : (
          <div className="proofSummaryBadge proofSummaryBadge--neutral">
            Live deployment status shown below · detailed public verification artifacts are not currently published
          </div>
        )}
      </header>

      {/* ── Proof cards ──────────────────────────────────────── */}
      <div className="proofGrid">
        {[deploymentCard, ...publishedCards].map((card) => (
          <article key={card.title} className={`proofCard proofCard--${card.status}`}>
            <div className="proofCardHeader">
              <h2 className="proofCardTitle">{card.title}</h2>
              <span className={`proofCardStatus proofCardStatus--${card.status}`}>{card.statusLabel}</span>
            </div>
            <table className="proofCardTable">
              <tbody>
                {card.lines.map((line) => (
                  <tr key={line.label + line.value}>
                    <td className="proofCardLabel">{line.label}</td>
                    <td className="proofCardValue">{line.value}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {card.note && <p className="proofCardNote">{card.note}</p>}
          </article>
        ))}
      </div>

      {/* ── Public verification evidence status ───────────────── */}
      {!hasPublished && (
        <section className="proofHonestyNote">
          <h2 className="proofHonestyTitle">Public verification evidence</h2>
          <p className="proofHeroSubtitle" style={{ margin: '0 0 0.75rem' }}>
            Decoda&rsquo;s detailed CI and staging live-evidence proof artifacts (readiness gates, live-provider
            telemetry, the alert → incident → evidence chain, and release gates) are produced by internal pipelines and
            are not currently published to this public deployment. Rather than show placeholder or fabricated results,
            this page shows only what can be verified live from the running system.
          </p>
          <ul className="proofHonestyList">
            <li>The live deployment card above reflects the real environment, commit, and configuration serving this page.</li>
            <li>Detailed proof artifacts are available to Design Partners and reviewers on request during evaluation.</li>
            <li>When a proof run is attached to a public deployment, its genuine results render above automatically.</li>
          </ul>
        </section>
      )}

      {/* ── Artifact honesty note ─────────────────────────────── */}
      <section className="proofHonestyNote">
        <h2 className="proofHonestyTitle">How this page works</h2>
        <ul className="proofHonestyList">
          <li>The Build &amp; Deployment card is read at server-render time from the live deployment environment serving this request.</li>
          <li>Proof cards are read from JSON artifacts produced by our CI and staging live-evidence pipelines, which run the full evidence chain against a live Base Mainnet RPC endpoint.</li>
          <li>When a proof artifact is not attached to this deployment, the card is omitted rather than shown as passing, failing, or pending.</li>
          <li>No chart or status is fabricated. Any UUIDs shown are from actual CI runs.</li>
          <li>Simulator or seeded data is never used on this page.</li>
        </ul>
      </section>

      {/* ── Links ────────────────────────────────────────────── */}
      <div className="trustFooterLinks">
        <Link href="/" prefetch={false} className="trustLink">← Home</Link>
        <Link href="/evidence" prefetch={false} className="trustLink">Evidence viewer</Link>
        <Link href="/trust" prefetch={false} className="trustLink">Security &amp; Trust</Link>
        <Link href="/sign-up" prefetch={false} className="trustLink">Start monitoring</Link>
      </div>
    </main>
  );
}
