'use client';

import Link from 'next/link';
import { useState } from 'react';

import { DecodaLogo, HomeIcon } from './home-icons';
import { ROUTES } from './home-data';
import styles from './home.module.css';

const NAV_LINKS = [
  { label: 'Company', href: ROUTES.companyAnchor },
  { label: 'RWA Security', href: ROUTES.rwaAnchor },
  { label: 'Platform Vision', href: ROUTES.platformVisionAnchor },
  { label: 'Pricing', href: ROUTES.pricing },
  { label: 'Contact', href: ROUTES.contactAnchor },
];

/** Decoda Security brand lockup — shield mark + two-line wordmark. */
export function BrandLockup() {
  return (
    <>
      <DecodaLogo className={styles.brandMark} />
      <span className={styles.brandText}>
        <span className={styles.brandName}>DECODA</span>
        <span className={styles.brandSub}>SECURITY</span>
      </span>
    </>
  );
}

function NavItem({ label, href }: { label: string; href: string }) {
  // Real routes render as Next links; on-page anchors stay plain <a>.
  if (href.startsWith('#')) {
    return (
      <a href={href} className={styles.navLink}>
        {label}
      </a>
    );
  }
  return (
    <Link href={href} className={styles.navLink} prefetch={false}>
      {label}
    </Link>
  );
}

export function MarketingHeader() {
  const [open, setOpen] = useState(false);
  const close = () => setOpen(false);

  return (
    <header className={styles.header}>
      <div className={styles.headerInner}>
        <Link href="/" className={styles.brand} prefetch={false} onClick={close} aria-label="Decoda Security home">
          <BrandLockup />
        </Link>

        <nav className={styles.navCenter} aria-label="Primary">
          {NAV_LINKS.map((link) => (
            <NavItem key={link.label} label={link.label} href={link.href} />
          ))}
        </nav>

        <div className={styles.navRight}>
          <a href={ROUTES.demoMailto} className={`${styles.btnPrimary} ${styles.headerCta}`}>
            Request a demo
            <HomeIcon name="arrowRight" className={styles.btnArrow} />
          </a>
        </div>

        <button
          type="button"
          className={styles.hamburger}
          aria-label={open ? 'Close navigation menu' : 'Open navigation menu'}
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
        >
          <span className={styles.hamburgerBar} aria-hidden="true" />
          <span className={styles.hamburgerBar} aria-hidden="true" />
          <span className={styles.hamburgerBar} aria-hidden="true" />
        </button>
      </div>

      <div className={`${styles.mobilePanel}${open ? ` ${styles.mobilePanelOpen}` : ''}`}>
        {NAV_LINKS.map((link) =>
          link.href.startsWith('#') ? (
            <a key={link.label} href={link.href} className={styles.mobileLink} onClick={close}>
              {link.label}
            </a>
          ) : (
            <Link key={link.label} href={link.href} className={styles.mobileLink} prefetch={false} onClick={close}>
              {link.label}
            </Link>
          ),
        )}
        <div className={styles.mobileRow}>
          <a href={ROUTES.demoMailto} className={styles.btnPrimary} onClick={close}>
            Request a demo
          </a>
        </div>
      </div>
    </header>
  );
}
