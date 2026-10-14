'use client';

import Link from 'next/link';
import { useCallback, useEffect, useRef, useState } from 'react';

import type { LandingSessionHint } from '../../auth-guards';
import { AuthNav } from './auth-nav';
import { DecodaLogo } from './decoda-logo';
import { HomeIcon } from './home-icons';
import { NAV_ITEMS, type NavKey } from './home-data';
import styles from './site.module.css';

/** Above this width the desktop navigation is shown and the sheet must close. */
const DESKTOP_QUERY = '(min-width: 981px)';

/**
 * The public site header — the company website's header, carrying the Guard
 * lockup.
 *
 *  • Glass background: clear at the top of the page, solid once it scrolls.
 *    A 1px sentinel above the sticky header flips `data-scrolled`, so this
 *    costs nothing per scroll frame.
 *  • Section highlight: on the homepage, the nav item whose section is in the
 *    middle of the screen gets `data-active`. Other pages pass `active`.
 *  • Auth-aware actions: <AuthNav> renders "Sign in" + "Request Pilot" for a
 *    visitor, "Dashboard" + the account menu for a confirmed session, and a
 *    neutral placeholder while that is still being checked.
 *  • Mobile: a full-screen sheet behind a menu button wired with
 *    aria-expanded/aria-controls; Escape, any link, or widening the window to
 *    desktop closes it, and the page behind it does not scroll.
 */
export function MarketingHeader({
  sessionHint,
  active,
}: {
  sessionHint: LandingSessionHint;
  active?: NavKey;
}) {
  const [open, setOpen] = useState(false);
  const [current, setCurrent] = useState<NavKey | undefined>(active);
  const sentinelRef = useRef<HTMLDivElement | null>(null);
  const headerRef = useRef<HTMLElement | null>(null);
  const buttonRef = useRef<HTMLButtonElement | null>(null);
  const close = useCallback(() => setOpen(false), []);

  // Glass background once the page has scrolled.
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

  // Homepage section highlight: whichever tracked section crosses the band in
  // the middle of the viewport is current. Pages without the sections keep
  // the `active` they were given.
  useEffect(() => {
    if (active || typeof IntersectionObserver === 'undefined') {
      return undefined;
    }
    const tracked = NAV_ITEMS.flatMap((item) => {
      const el = item.section ? document.getElementById(item.section) : null;
      return el ? [{ key: item.key, el }] : [];
    });
    if (tracked.length === 0) {
      return undefined;
    }
    const io = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          const match = tracked.find((item) => item.el === entry.target);
          if (!match) continue;
          if (entry.isIntersecting) {
            setCurrent(match.key);
          } else {
            setCurrent((value) => (value === match.key ? undefined : value));
          }
        }
      },
      { rootMargin: '-45% 0px -50% 0px' },
    );
    for (const item of tracked) io.observe(item.el);
    return () => io.disconnect();
  }, [active]);

  // While the sheet is open: Escape closes it (focus back on the button), and
  // widening the window to desktop closes it.
  useEffect(() => {
    if (!open) {
      return undefined;
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        setOpen(false);
        buttonRef.current?.focus();
      }
    }
    const desktop = typeof window.matchMedia === 'function' ? window.matchMedia(DESKTOP_QUERY) : null;
    const onDesktop = (event: MediaQueryListEvent) => {
      if (event.matches) setOpen(false);
    };
    document.addEventListener('keydown', onKeyDown);
    desktop?.addEventListener('change', onDesktop);
    return () => {
      document.removeEventListener('keydown', onKeyDown);
      desktop?.removeEventListener('change', onDesktop);
    };
  }, [open]);

  return (
    <>
      <div ref={sentinelRef} className={styles.headerSentinel} aria-hidden="true" />

      <header ref={headerRef} className={styles.header} data-scrolled="false" data-menu={open ? 'open' : 'closed'}>
        <div className={styles.headerInner}>
          <Link href="/" className={styles.brand} prefetch={false} aria-label="Decoda RWA Guard — home" onClick={close}>
            <DecodaLogo />
          </Link>

          <nav className={styles.nav} aria-label="Primary">
            {NAV_ITEMS.map((item) => (
              <a
                key={item.key}
                href={item.href}
                className={styles.navLink}
                data-active={current === item.key ? 'true' : undefined}
                aria-current={active === item.key ? 'page' : undefined}
              >
                {item.label}
              </a>
            ))}
          </nav>

          <div className={styles.actions}>
            <AuthNav sessionHint={sessionHint} variant="desktop" />

            <button
              ref={buttonRef}
              type="button"
              className={styles.menuButton}
              aria-label={open ? 'Close navigation menu' : 'Open navigation menu'}
              aria-expanded={open}
              aria-controls="marketing-mobile-nav"
              onClick={() => setOpen((value) => !value)}
            >
              <span className={styles.menuGlyph} aria-hidden="true">
                <span />
                <span />
              </span>
            </button>
          </div>
        </div>

        <div id="marketing-mobile-nav" className={`${styles.menuSheet}${open ? ` ${styles.menuOpen}` : ''}`}>
          <nav className={styles.menuPanel} aria-label="Primary (mobile)">
            <p className={styles.menuLabel}>Decoda RWA Guard</p>
            <ul className={styles.menuLinks}>
              {NAV_ITEMS.map((item) => (
                <li key={item.key}>
                  <a href={item.href} className={styles.menuLink} onClick={close}>
                    {item.label}
                    <HomeIcon name="arrowRight" size={16} />
                  </a>
                </li>
              ))}
            </ul>
            <AuthNav sessionHint={sessionHint} variant="mobile" onNavigate={close} />
          </nav>
        </div>
      </header>
    </>
  );
}
