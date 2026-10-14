import type { Metadata, Viewport } from 'next';
import Link from 'next/link';

import { ROUTES } from '../components/home/home-data';
import { PlanCards } from '../components/home/plan-cards';
import { PublicShell } from '../components/home/public-shell';
import { Reveal } from '../components/home/scroll-reveal';
import { Faq, PlanCompare, Steps, type FaqItem, type Step } from '../components/home/site-blocks';
import { CtaBand, PageHero, SectionHeading } from '../components/home/site-ui';
import site from '../components/home/site.module.css';
import pages from '../components/home/pages.module.css';
import { EXECUTION_NOTE, PRICING_NOTE } from '../pricing-plans';
import { publicPageMetadata } from '../public-metadata';
import { DECODA_SITE } from '../public-site-links';

export const dynamic = 'force-dynamic';

export const metadata: Metadata = publicPageMetadata({
  title: 'Pricing',
  description: `Plans for Decoda RWA Guard. ${PRICING_NOTE}`,
  path: '/pricing',
});

export const viewport: Viewport = { themeColor: '#ffffff', colorScheme: 'light' };

/**
 * How a Pilot starts. Approval-only: nothing is provisioned until Decoda has
 * reviewed the request and the applicant accepts the invitation.
 */
const PILOT_STEPS: Step[] = [
  {
    icon: 'mail',
    title: 'You request a pilot',
    body: 'Tell us about your team, your program and what you want to monitor.',
  },
  {
    icon: 'investigate',
    title: 'Decoda reviews and scopes it',
    body: 'The Decoda team reviews every request and agrees the monitoring scope and terms with you.',
  },
  {
    icon: 'workflow',
    title: 'Your organization is set up',
    body: 'If the request is approved, Decoda enables Guard for your organization — nothing is provisioned before.',
  },
  {
    icon: 'approval',
    title: 'You accept the invitation',
    body: 'Your administrator accepts the invitation and sets up multi-factor sign-in.',
  },
  {
    icon: 'checkCircle',
    title: 'You evaluate and decide',
    body: 'Validate Guard against your own infrastructure, then decide whether to continue on Scale.',
  },
];

const FAQ: FaqItem[] = [
  {
    q: 'How is a pilot priced?',
    a: (
      <p>
        Pilot scope and commercial terms are agreed with each team before the evaluation starts.{' '}
        <a href={ROUTES.requestPilot}>Request a pilot</a> or <a href={DECODA_SITE.contact}>contact Decoda</a> to
        discuss yours.
      </p>
    ),
  },
  {
    q: 'How do I get a Pilot evaluation?',
    a: (
      <p>
        Submit a <a href="/request-pilot">Pilot request</a>. Decoda reviews each request individually and sends an
        invitation to approved applicants; the evaluation starts when you accept it.
        There is no self-service sign-up for the Pilot.
      </p>
    ),
  },
  {
    q: 'What does the Scale price include?',
    a: (
      <p>
        Scale starts at $999 per month for a defined scope — up to 3 workspaces and 25 monitored contracts. Final
        pricing depends on your monitoring scope and the support arrangements in your agreement.
      </p>
    ),
  },
  {
    q: 'What counts as a monitored contract?',
    a: (
      <p>
        A monitored contract is an asset you register in your workspace. Each one can carry several monitoring
        targets — for example its contract, related wallets and an oracle feed — up to an operational bound of two
        targets per monitored contract on Pilot and Scale.
      </p>
    ),
  },
  {
    q: 'Can I buy a plan online?',
    a: (
      <p>
        No. Every plan is arranged with the Decoda team, and the commercial terms are set out in your order or
        agreement.
      </p>
    ),
  },
  { q: 'Can Decoda execute transactions on our behalf?', a: <p>{EXECUTION_NOTE}</p> },
  {
    q: 'What happens to our data when a pilot ends?',
    a: (
      <p>
        Your workspace stays readable and your evidence stays exportable for 30 days after the Pilot ends; after
        that, operational data is deleted on the schedule set out in the{' '}
        <Link href={ROUTES.privacy} prefetch={false}>
          Privacy Policy
        </Link>
        . Continuing on a plan before then cancels the deletion.
      </p>
    ),
  },
  {
    q: 'Are Decoda Vault and Decoda Assets included?',
    a: (
      <p>
        No. Vault and Assets are earlier-stage products on public testnets. They are not commercially available and
        are not integrated with Guard.
      </p>
    ),
  },
  {
    q: 'Do plans include an SLA or certifications?',
    a: (
      <p>
        No plan includes an SLA or a compliance certification unless it is set out in your agreement. Our current
        security posture — including what we have not yet achieved — is on{' '}
        <Link href={ROUTES.trust} prefetch={false}>
          Security &amp; Trust
        </Link>
        .
      </p>
    ),
  },
];

/**
 * Public pricing. Plans, prices, CTAs and the comparison all render from the
 * canonical shared config (app/pricing-plans.ts), aligned with the company
 * website's pricing.
 */
export default async function PricingPage() {
  return (
    <PublicShell active="pricing">
      <PageHero
        eyebrow="Pricing"
        title="Start with a Scoped Pilot. Scale When You’re Ready."
        lead="Pricing for Decoda RWA Guard. Evaluate against your own infrastructure first, then move to recurring security operations — every plan is arranged with the Decoda team."
      />

      <section className={`${pages.band} ${pages.bandFirst}`} aria-label="Plans">
        <div className={site.container}>
          <PlanCards headingLevel={2} />
          <Reveal as="p" className={pages.note}>
            {EXECUTION_NOTE}
          </Reveal>
        </div>
      </section>

      <section className={`${pages.band} ${pages.bandSoft}`} aria-labelledby="compare-title">
        <div className={site.container}>
          <SectionHeading id="compare-title" eyebrow="Compare plans" title="What Each Plan Includes." />
          <PlanCompare />
          <p className={pages.note}>
            Limits reflect the plan entitlements Decoda RWA Guard enforces. Enterprise scope is agreed per
            organization. No plan includes an SLA or compliance certification unless it is set out in your agreement.
          </p>
        </div>
      </section>

      <section className={pages.band} aria-labelledby="pilot-steps-title">
        <div className={site.container}>
          <SectionHeading
            id="pilot-steps-title"
            eyebrow="How a pilot works"
            title="Reviewed, Scoped, Then Provisioned."
            lead="Decoda RWA Guard is invitation-only. Nothing is provisioned until a request has been reviewed and approved."
          />
          <Steps steps={PILOT_STEPS} label="How a pilot works" />
        </div>
      </section>

      <section className={`${pages.band} ${pages.bandSoft}`} aria-labelledby="pricing-faq-title">
        <div className={site.container}>
          <SectionHeading id="pricing-faq-title" align="center" eyebrow="FAQ" title="Questions About Plans." />
          <Faq items={FAQ} />
        </div>
      </section>

      <CtaBand
        id="pricing-cta-title"
        title="Evaluate Guard Against Your Own Infrastructure."
        body="Request a scoped pilot. The Decoda team reviews every request and agrees the monitoring scope with you before anything is provisioned."
        primary={{ label: 'Request a Pilot', href: ROUTES.requestPilot }}
        secondary={{ label: 'Contact sales', href: DECODA_SITE.contact }}
      />
    </PublicShell>
  );
}
