import Link from 'next/link';
import type { ReactNode } from 'react';

import { DecodaShield } from './decoda-logo';
import type { ProductTone } from './home-data';
import { HomeIcon } from './home-icons';
import { Reveal } from './scroll-reveal';
import styles from './site.module.css';

/* ------------------------------- Headings -------------------------------- */

export function SectionHeading({
  eyebrow,
  title,
  lead,
  id,
  align = 'start',
  className,
  children,
}: {
  eyebrow?: string;
  title: ReactNode;
  lead?: ReactNode;
  /** id of the <h2>, for the section's aria-labelledby. */
  id?: string;
  align?: 'start' | 'center';
  className?: string;
  children?: ReactNode;
}) {
  const classes = [styles.head, align === 'center' ? styles.headCenter : '', className ?? '']
    .filter(Boolean)
    .join(' ');
  return (
    <Reveal className={classes}>
      {eyebrow ? <p className={styles.eyebrow}>{eyebrow}</p> : null}
      <h2 id={id} className={styles.title}>
        {title}
      </h2>
      {lead ? <p className={styles.lead}>{lead}</p> : null}
      {children}
    </Reveal>
  );
}

/* --------------------------------- Badges -------------------------------- */

export type BadgeTone = ProductTone | 'implemented' | 'planned' | 'neutral';

export function StatusBadge({
  tone,
  children,
  size = 'md',
  className,
}: {
  tone: BadgeTone;
  children: ReactNode;
  size?: 'sm' | 'md';
  className?: string;
}) {
  const classes = [styles.badge, size === 'sm' ? styles.badgeSm : '', className ?? ''].filter(Boolean).join(' ');
  return (
    <span className={classes} data-tone={tone}>
      {children}
    </span>
  );
}

/** Marks a visual as an illustration with sample data, never live product data. */
export function IllustrativeTag({
  children = 'Illustrative · sample data',
  className,
}: {
  children?: ReactNode;
  className?: string;
}) {
  return <span className={className ? `${styles.illustrative} ${className}` : styles.illustrative}>{children}</span>;
}

/* ------------------------------- Backdrops ------------------------------- */

/** Grid texture plus slow ambient light behind a hero. Purely decorative. */
export function AmbientBackdrop({ mint = false }: { mint?: boolean }) {
  return (
    <div className={styles.backdrop} aria-hidden="true">
      <div className={styles.grid} />
      <div className={`${styles.glow} ${styles.glowTeal}`} />
      <div className={`${styles.glow} ${styles.glowCyan}`} />
      {mint ? <div className={`${styles.glow} ${styles.glowMint}`} /> : null}
    </div>
  );
}

/* ------------------------------- Page hero ------------------------------- */

/** The hero for every inner public page: light, textured, staggered on load. */
export function PageHero({
  eyebrow,
  title,
  lead,
  actions,
  meta,
  align = 'start',
  titleId = 'page-title',
}: {
  eyebrow: string;
  title: ReactNode;
  lead?: ReactNode;
  actions?: ReactNode;
  meta?: ReactNode;
  align?: 'start' | 'center';
  titleId?: string;
}) {
  return (
    <section className={styles.pageHero} aria-labelledby={titleId}>
      <AmbientBackdrop />
      <div className={styles.container}>
        <div className={`${styles.pageHeroInner} ${align === 'center' ? styles.pageHeroCenter : ''} ${styles.enter}`}>
          <p className={styles.eyebrow}>{eyebrow}</p>
          <h1 id={titleId} className={styles.pageHeroTitle}>
            {title}
          </h1>
          {lead ? <p className={styles.pageHeroLead}>{lead}</p> : null}
          {actions ? <div className={styles.pageHeroActions}>{actions}</div> : null}
          {meta ? <div className={styles.pageHeroMeta}>{meta}</div> : null}
        </div>
      </div>
    </section>
  );
}

/* --------------------------------- Links --------------------------------- */

export type Action = { href: string; label: string };

function isExternal(href: string): boolean {
  return /^(https?:|mailto:)/.test(href);
}

/**
 * A call-to-action link. Internal pages use next/link; external destinations,
 * and routes that may hand off to the company website (/request-pilot), are
 * plain anchors so the browser performs a normal navigation.
 */
export function ActionLink({
  action,
  variant,
  large = false,
  className,
}: {
  action: Action;
  variant: 'primary' | 'secondary';
  large?: boolean;
  className?: string;
}) {
  const classes = [
    variant === 'primary' ? styles.btnPrimary : styles.btnSecondary,
    large ? styles.btnLg : '',
    className ?? '',
  ]
    .filter(Boolean)
    .join(' ');
  const external = isExternal(action.href);
  const content = (
    <>
      {action.label}
      {external && variant === 'secondary' ? (
        <HomeIcon name="arrowUpRight" size={16} />
      ) : variant === 'primary' ? (
        <HomeIcon name="arrowRight" size={17} />
      ) : null}
    </>
  );
  if (external || action.href.startsWith('/request-pilot') || action.href.startsWith('/#')) {
    return (
      <a href={action.href} className={classes}>
        {content}
      </a>
    );
  }
  return (
    <Link href={action.href} className={classes} prefetch={false}>
      {content}
    </Link>
  );
}

export function TextLink({ href, children, className }: { href: string; children: ReactNode; className?: string }) {
  const classes = className ? `${styles.textLink} ${className}` : styles.textLink;
  const external = isExternal(href);
  return (
    <a href={href} className={classes}>
      {children}
      <HomeIcon name={external ? 'arrowUpRight' : 'arrowRight'} size={16} />
    </a>
  );
}

/* -------------------------------- CTA band ------------------------------- */

/** The dark closing call to action, as on every page of the company website. */
export function CtaBand({
  title,
  body,
  primary,
  secondary,
  id = 'closing-cta',
}: {
  title: ReactNode;
  body: ReactNode;
  primary: Action;
  secondary?: Action;
  id?: string;
}) {
  return (
    <section className={styles.ctaWrap} aria-labelledby={id}>
      <div className={styles.container}>
        <Reveal variant="scale" className={`${styles.surfaceDark} ${styles.cta}`}>
          <div className={styles.ctaGrid} aria-hidden="true" />
          <div className={styles.ctaOrbit} aria-hidden="true">
            <div className={styles.ctaSweep} />
          </div>
          <div>
            <DecodaShield className={styles.ctaShield} strong />
            <h2 id={id} className={styles.ctaTitle}>
              {title}
            </h2>
            <p className={styles.ctaBody}>{body}</p>
          </div>
          <div className={styles.ctaActions}>
            <ActionLink action={primary} variant="primary" large />
            {secondary ? <ActionLink action={secondary} variant="secondary" large /> : null}
          </div>
        </Reveal>
      </div>
    </section>
  );
}
