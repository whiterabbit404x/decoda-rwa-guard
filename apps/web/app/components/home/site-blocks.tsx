import type { ReactNode } from 'react';

import { PRICING_PLANS } from '../../pricing-plans';
import { HomeIcon, type IconName } from './home-icons';
import { Reveal, RevealGroup } from './scroll-reveal';
import { PageHero } from './site-ui';
import site from './site.module.css';
import pages from './pages.module.css';
import plans from './plans.module.css';

/**
 * Content blocks shared by the inner public pages. Server components; the
 * only motion is the shared one-time reveal and the native <details> toggle,
 * so every block is readable without JavaScript.
 */

/* ---------------------------------- FAQ ---------------------------------- */

export type FaqItem = { q: string; a: ReactNode };

/** Native disclosure widgets: keyboard and screen-reader support built in. */
export function Faq({ items }: { items: FaqItem[] }) {
  return (
    <RevealGroup className={`${pages.faq} ${site.staggerTight}`}>
      {items.map((item) => (
        <details key={item.q} className={pages.faqItem}>
          <summary>
            {item.q}
            <span className={pages.faqIcon} aria-hidden="true">
              <HomeIcon name="plus" size={14} />
            </span>
          </summary>
          <div className={pages.faqBody}>{item.a}</div>
        </details>
      ))}
    </RevealGroup>
  );
}

/* --------------------------------- Steps --------------------------------- */

export type Step = { icon: IconName; title: string; body: string };

export function Steps({ steps, label }: { steps: Step[]; label: string }) {
  return (
    <RevealGroup as="ol" className={`${pages.steps} ${site.staggerWide}`} aria-label={label}>
      {steps.map((step, index) => (
        <li key={step.title} className={pages.step}>
          <div className={pages.stepTop}>
            <span className={pages.stepIcon} aria-hidden="true">
              <HomeIcon name={step.icon} size={18} />
            </span>
            <span className={pages.stepNum}>{String(index + 1).padStart(2, '0')}</span>
          </div>
          <h3>{step.title}</h3>
          <p>{step.body}</p>
        </li>
      ))}
    </RevealGroup>
  );
}

/* --------------------------- Plan comparison ----------------------------- */

/** The full comparison, rows aligned by index across PRICING_PLANS. */
export function PlanCompare() {
  const rows = PRICING_PLANS[0].comparison.map((row) => row.label);
  return (
    <Reveal className={plans.tableWrap}>
      <table className={plans.table}>
        <caption className={site.srOnly}>Decoda RWA Guard plan comparison</caption>
        <thead>
          <tr>
            <th scope="col">Capability</th>
            {PRICING_PLANS.map((plan) => (
              <th key={plan.key} scope="col" data-recommended={plan.featured ? 'true' : undefined}>
                {plan.tier}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((label, index) => (
            <tr key={label}>
              <th scope="row">{label}</th>
              {PRICING_PLANS.map((plan) => {
                const value = plan.comparison[index].value;
                return (
                  <td key={plan.key} data-recommended={plan.featured ? 'true' : undefined}>
                    {value === '✓' ? (
                      <span className={plans.yes}>
                        <HomeIcon name="check" size={13} />
                        <span className={site.srOnly}>Included</span>
                      </span>
                    ) : value === '—' ? (
                      <span className={plans.no}>
                        —<span className={site.srOnly}>Not included</span>
                      </span>
                    ) : (
                      value
                    )}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </Reveal>
  );
}

/* ------------------------------- Documents ------------------------------- */

/**
 * The frame for a legal or support document: the inner-page hero carrying the
 * title and the "last updated" date, then the document itself in a readable
 * column. The document's own markup (h2, p, ul, table, links) is styled by
 * pages.module.css `.doc`.
 */
export function LegalDocument({
  eyebrow,
  title,
  updated,
  lead,
  children,
}: {
  eyebrow: string;
  title: string;
  updated: string;
  lead?: string;
  children: ReactNode;
}) {
  return (
    <>
      <PageHero
        eyebrow={eyebrow}
        title={title}
        lead={lead}
        meta={<span className={pages.docMeta}>Last updated: {updated}</span>}
      />
      <section className={`${pages.band} ${pages.bandFirst}`} aria-label={title}>
        <div className={site.container}>
          <Reveal className={pages.docLayout}>
            <div className={pages.doc}>{children}</div>
          </Reveal>
        </div>
      </section>
    </>
  );
}
