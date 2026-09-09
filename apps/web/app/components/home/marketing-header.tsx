'use client';

import Link from 'next/link';
import { useState } from 'react';

import type { LandingSessionHint } from '../../auth-guards';
import { AuthNav } from './auth-nav';
import { DecodaLogo } from './home-icons';
import { ROUTES } from './home-data';
import styles from './home.module.css';

const NAV_LINKS = [
  { label: 'Platform', href: ROUTES.platformAnchor },
  { label: 'How it works', href: ROUTES.howItWorksAnchor },
  { label: 'RWA Security', href: ROUTES.rwaAnchor },
  { label: 'Pricing', href: ROUTES.pricingAnchor },
];

export function MarketingHeader({ sessionHint }: { sessionHint: LandingSessionHint }) {
  const [open, setOpen] = useState(false);
  const close = () => setOpen(false);

  return (
    <header className={styles.header}>
      <div className={styles.headerInner}>
        <Link href="/" className={styles.brand} prefetch={false} onClick={close}>
          <DecodaLogo className={styles.brandMark} />
          <span className={styles.brandName}>DECODA</span>
        </Link>

        <nav className={styles.navCenter} aria-label="Primary">
          {NAV_LINKS.map((link) => (
            <a key={link.label} href={link.href} className={styles.navLink}>
              {link.label}
            </a>
          ))}
        </nav>

        <AuthNav sessionHint={sessionHint} variant="desktop" />

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
        {NAV_LINKS.map((link) => (
          <a key={link.label} href={link.href} className={styles.mobileLink} onClick={close}>
            {link.label}
          </a>
        ))}
        <AuthNav sessionHint={sessionHint} variant="mobile" onNavigate={close} />
      </div>
    </header>
  );
}
