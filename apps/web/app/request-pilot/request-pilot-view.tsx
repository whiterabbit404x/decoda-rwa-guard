import Link from 'next/link';

import { ROUTES } from '../components/home/home-data';
import { HomeIcon } from '../components/home/home-icons';
import { PublicShell } from '../components/home/public-shell';
import { Reveal } from '../components/home/scroll-reveal';
import { PageHero } from '../components/home/site-ui';
import site from '../components/home/site.module.css';
import pages from '../components/home/pages.module.css';
import { REVIEW_NOTICE } from '../pilot-request-copy';
import { DECODA_SITE } from '../public-site-links';

import RequestPilotClient from './request-pilot-client';

/** The same review-first sequence the pricing page describes. */
const NEXT_STEPS = [
  'Decoda reviews your request.',
  'We agree the monitoring scope and terms with your team.',
  'If it is approved, you receive an invitation by email.',
  'Your administrator accepts it and sets up multi-factor sign-in.',
];

/**
 * The Pilot application page as the legacy identity mode serves it: the form
 * first, in reading and tab order, and beside it what happens after you send
 * it. page.tsx decides whether this renders at all.
 */
export function RequestPilotView() {
  return (
    <PublicShell>
      <PageHero eyebrow="Pilot evaluation" title="Request a Pilot Evaluation." lead={REVIEW_NOTICE} />

      <section className={`${pages.band} ${pages.bandFirst}`} aria-label="Pilot request">
        <div className={site.container}>
          <div className={pages.formGrid}>
            <RequestPilotClient />

            <Reveal as="aside" className={pages.formAside} aria-label="About the Pilot">
              <div className={pages.asideBlock}>
                <h2 className={pages.asideTitle}>What happens next</h2>
                <ol>
                  {NEXT_STEPS.map((step) => (
                    <li key={step}>{step}</li>
                  ))}
                </ol>
              </div>
              <div className={pages.asideBlock}>
                <h2 className={pages.asideTitle}>What a Pilot covers</h2>
                <p>
                  A scoped evaluation of Guard against your own infrastructure: monitoring, alerts, incident
                  investigation and evidence workflows. Response workflows stay recommend-only — nothing executes in
                  production.
                </p>
              </div>
              <div className={pages.asideBlock}>
                <h2 className={pages.asideTitle}>Questions first?</h2>
                <p>
                  See how plans work on the{' '}
                  <Link href={ROUTES.pricing} prefetch={false}>
                    pricing page
                  </Link>
                  , or{' '}
                  <a href={DECODA_SITE.contact}>
                    contact Decoda
                    <HomeIcon name="arrowUpRight" size={13} />
                  </a>
                  .
                </p>
              </div>
            </Reveal>
          </div>
        </div>
      </section>
    </PublicShell>
  );
}
