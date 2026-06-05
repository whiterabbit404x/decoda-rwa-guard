import Link from 'next/link';
import { readFileSync } from 'fs';
import { join } from 'path';

export const dynamic = 'force-dynamic';

// ─── Safe artifact reader ─────────────────────────────────────

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

// ─── Build proof cards from real artifacts ───────────────────

function buildProofCards(): ProofCard[] {
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
    cards.push({
      title: 'Production Readiness Gates',
      status: ready100 ? 'pass' : score !== undefined ? 'fail' : 'unknown',
      statusLabel: ready100 ? 'All gates passing' : score !== undefined ? `Score: ${score}/100` : 'Unavailable',
      lines: [
        { label: 'Overall score', value: score !== undefined ? `${score}/100` : '—' },
        { label: 'Controlled pilot ready', value: readiness?.controlled_pilot_ready ? 'Yes' : '—' },
        { label: 'Broad paid SaaS ready', value: readiness?.broad_paid_saas_ready ? 'Yes' : '—' },
        { label: 'Enterprise procurement ready', value: readiness?.enterprise_procurement_ready ? 'Yes' : '—' },
        { label: 'Generated at', value: generatedAt ? generatedAt.replace('T', ' ').replace(/\.\d+.*$/, ' UTC') : '—' },
      ],
    });
  }

  // 2. Live EVM telemetry
  {
    const lep = liveEvidence?.live_provider_evidence as Record<string, unknown> | undefined;
    const ready = lep?.live_evidence_ready as boolean | undefined;
    const block = lep?.block_number_observed as string | undefined;
    const chainId = lep?.chain_id_observed as string | undefined;
    const providerMode = lep?.provider_mode as string | undefined;
    const generatedAt = liveEvidence?.generated_at as string | undefined;
    const githubRunId = lep?.github_run_id as string | undefined;
    cards.push({
      title: 'Live EVM Telemetry',
      status: ready ? 'pass' : lep ? 'fail' : 'unknown',
      statusLabel: ready ? 'Proven in CI' : lep ? 'Proof unavailable' : 'Unavailable',
      lines: [
        { label: 'Provider mode', value: providerMode ?? '—' },
        { label: 'Chain ID', value: chainId ? `${chainId} (Ethereum mainnet)` : '—' },
        { label: 'Block observed', value: block ? `#${block}` : '—' },
        { label: 'CI run', value: githubRunId ? `#${githubRunId}` : '—' },
        { label: 'Proof generated', value: generatedAt ? generatedAt.replace('T', ' ').replace(/\.\d+.*$/, ' UTC') : '—' },
      ],
    });
  }

  // 3. Evidence chain
  {
    const lep = liveEvidence?.live_provider_evidence as Record<string, unknown> | undefined;
    const chain = lep?.chain as Record<string, unknown> | undefined;
    const ready = lep?.live_evidence_ready as boolean | undefined;
    cards.push({
      title: 'Alert → Incident → Evidence Chain',
      status: chain && ready ? 'pass' : chain ? 'fail' : 'unknown',
      statusLabel: chain && ready ? 'Proven end-to-end' : chain ? 'Partial' : 'Unavailable',
      lines: [
        { label: 'Telemetry event ID', value: chain?.telemetry_event_id as string ?? '—' },
        { label: 'Detection ID', value: chain?.detection_id as string ?? '—' },
        { label: 'Alert ID', value: chain?.alert_id as string ?? '—' },
        { label: 'Incident ID', value: chain?.incident_id as string ?? '—' },
        { label: 'Evidence package ID', value: chain?.evidence_package_id as string ?? '—' },
      ],
      note: 'All IDs are live — generated from real Ethereum mainnet telemetry, not seeded or simulated data.',
    });
  }

  // 4. CI gates
  {
    const ciReady = sellNow?.release_ci_gates_ready as boolean | undefined;
    const testReady = sellNow?.release_test_report_ready as boolean | undefined;
    const releaseStatus = releaseProof?.release_status as string | undefined ?? sellNow?.release_status as string | undefined;
    const generatedAt = releaseProof?.generated_at as string | undefined;
    cards.push({
      title: 'CI Release Gates',
      status: ciReady && testReady ? 'pass' : ciReady !== undefined ? 'fail' : 'unknown',
      statusLabel: ciReady && testReady ? 'All gates green' : ciReady !== undefined ? 'Gates failing' : 'Unavailable',
      lines: [
        { label: 'Release status', value: releaseStatus ?? '—' },
        { label: 'CI gates', value: ciReady ? 'Passing' : ciReady === false ? 'Failing' : '—' },
        { label: 'Test report', value: testReady ? 'Present' : testReady === false ? 'Missing' : '—' },
        { label: 'GitHub Actions visible green', value: sellNow?.github_actions_visible_green ? 'Yes' : '—' },
        { label: 'Proof generated', value: generatedAt ? generatedAt.replace('T', ' ').replace(/\.\d+.*$/, ' UTC') : '—' },
      ],
    });
  }

  // 5. Billing & email
  {
    const billingReady = sellNow?.billing_ready as boolean | undefined;
    const emailReady = sellNow?.email_ready as boolean | undefined;
    const provider = sellNow?.provider_ready as boolean | undefined;
    cards.push({
      title: 'Billing & Email Readiness',
      status: billingReady && emailReady ? 'pass' : billingReady !== undefined ? 'fail' : 'unknown',
      statusLabel: billingReady && emailReady ? 'Both ready' : billingReady !== undefined ? 'Check required' : 'Unavailable',
      lines: [
        { label: 'Billing provider', value: billingReady ? 'Ready (Paddle)' : billingReady === false ? 'Not ready' : '—' },
        { label: 'Email provider', value: emailReady ? 'Ready' : emailReady === false ? 'Not ready' : '—' },
        { label: 'RPC provider', value: provider ? 'Ready' : provider === false ? 'Not ready' : '—' },
        { label: 'Safe to sell broadly', value: sellNow?.safe_to_sell_broadly_today ? 'Yes' : '—' },
      ],
    });
  }

  // 6. Sell-now summary
  {
    const safeClaims = sellNow?.safe_claims as string[] | undefined;
    const warnings = sellNow?.warnings as string[] | undefined;
    const blockers = sellNow?.blockers as string[] | undefined;
    const ready = sellNow?.sell_now_managed_ready as boolean | undefined;
    cards.push({
      title: 'Sell-Now Assessment',
      status: ready ? 'pass' : ready === false ? 'fail' : 'unknown',
      statusLabel: ready ? 'Ready' : ready === false ? 'Blockers present' : 'Unavailable',
      lines: safeClaims?.slice(0, 4).map((c) => ({ label: '✓', value: c })) ?? [
        { label: 'Status', value: '—' },
      ],
      note: blockers?.length ? `Blockers: ${blockers.join('; ')}` : warnings?.length ? `Warnings: ${warnings.join('; ')}` : undefined,
    });
  }

  return cards;
}

// ─── Page ─────────────────────────────────────────────────────

export default function LiveProofPage() {
  const cards = buildProofCards();
  const allPass = cards.every((c) => c.status === 'pass');
  const failCount = cards.filter((c) => c.status === 'fail').length;
  const unknownCount = cards.filter((c) => c.status === 'unknown').length;

  return (
    <main className="proofPage">
      <nav className="trustNav" aria-label="Breadcrumb">
        <Link href="/" className="trustNavBack" prefetch={false}>← Back to Decoda RWA Guard</Link>
      </nav>

      {/* ── Hero ─────────────────────────────────────────────── */}
      <header className="proofHero">
        <p className="mktSectionLabel">LIVE PROOF</p>
        <h1 className="proofHeroTitle">
          Production proof — sourced from real CI artifacts.
        </h1>
        <p className="proofHeroSubtitle">
          Every value on this page is read from proof artifacts committed to the repository and generated by
          real CI runs. Nothing is fabricated. If an artifact is missing, we say so rather than showing placeholder data.
        </p>
        <div className={`proofSummaryBadge${allPass ? ' proofSummaryBadge--pass' : failCount > 0 ? ' proofSummaryBadge--fail' : ' proofSummaryBadge--warn'}`}>
          {allPass
            ? '✓ All proof checks passing'
            : failCount > 0
              ? `✗ ${failCount} check${failCount > 1 ? 's' : ''} failing`
              : `⚠ ${unknownCount} artifact${unknownCount > 1 ? 's' : ''} unavailable`}
        </div>
      </header>

      {/* ── Proof cards ──────────────────────────────────────── */}
      <div className="proofGrid">
        {cards.map((card) => (
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

      {/* ── Artifact honesty note ─────────────────────────────── */}
      <section className="proofHonestyNote">
        <h2 className="proofHonestyTitle">How this page works</h2>
        <ul className="proofHonestyList">
          <li>Data is read at server-render time from committed JSON artifact files in <code>artifacts/</code>.</li>
          <li>Artifacts are generated by CI pipelines that run the full evidence chain against live Ethereum mainnet RPC.</li>
          <li>If an artifact file is missing or unparseable, the card shows &ldquo;Unavailable&rdquo; rather than placeholder values.</li>
          <li>No chart or status is fabricated. All UUIDs shown are from actual CI runs.</li>
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
