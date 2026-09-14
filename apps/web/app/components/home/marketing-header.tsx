'use client';

import Link from 'next/link';
import { useEffect, useRef, useState } from 'react';

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
  const sentinelRef = useRef<HTMLDivElement | null>(null);
  const headerRef = useRef<HTMLElement | null>(null);
  const close = () => setOpen(false);

  // The header gains its border only once the page has scrolled. Watching a 1px
  // sentinel above the sticky header keeps this off the scroll event loop, so it
  // costs nothing per frame.
  useEffect(() => {
    const sentinel = sentinelRef.current;
    const header = headerRef.current;
    if (!sentinel || !header || typeof IntersectionObserver === 'undefined') {
      return undefined;
    }

    const io = new IntersectionObserver(
      ([entry]) => {
        header.dataset.scrolled = entry.isIntersecting ? 'false' : 'true';
      },
      { threshold: 0 },
    );
    io.observe(sentinel);
    return () => io.disconnect();
  }, []);

  return (
    <>
      <div ref={sentinelRef} className={styles.headerSentinel} aria-hidden="true" />

      <header ref={headerRef} className={`${styles.header} ${styles.zoneDark}`} data-scrolled="false">
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
            aria-controls="marketing-mobile-nav"
            onClick={() => setOpen((v) => !v)}
          >
            <span className={styles.hamburgerBar} aria-hidden="true" />
            <span className={styles.hamburgerBar} aria-hidden="true" />
            <span className={styles.hamburgerBar} aria-hidden="true" />
          </button>
        </div>

        <div
          id="marketing-mobile-nav"
          className={`${styles.mobilePanel}${open ? ` ${styles.mobilePanelOpen}` : ''}`}
        >
          {NAV_LINKS.map((link) => (
            <a key={link.label} href={link.href} className={styles.mobileLink} onClick={close}>
              {link.label}
            </a>
          ))}
          <AuthNav sessionHint={sessionHint} variant="mobile" onNavigate={close} />
        </div>
      </header>
    </>
  );
}
