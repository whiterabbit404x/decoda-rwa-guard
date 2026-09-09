'use client';

import Link from 'next/link';
import { useCallback, useEffect, useRef, useState } from 'react';

import type { LandingSessionHint } from '../../auth-guards';
import { accountDisplayName, accountInitials } from './auth-nav-state';
import { ROUTES } from './home-data';
import { useLandingAuth } from './use-landing-auth';
import styles from './home.module.css';

export type AuthNavVariant = 'desktop' | 'mobile';

/**
 * Auth-aware controls for the public marketing header.
 *
 * Signed out  → "Sign in" + "Start monitoring" (unchanged behaviour).
 * Signed in   → "Dashboard" + an account menu (name/email, Dashboard, Sign out).
 * Unresolved  → a neutral placeholder, so the signed-out controls never flash for a
 *               visitor who is in fact signed in.
 *
 * The authenticated visitor is NOT redirected away from the public page; only these
 * controls change.
 */
export function AuthNav({
  sessionHint,
  variant,
  onNavigate,
}: {
  sessionHint: LandingSessionHint;
  variant: AuthNavVariant;
  onNavigate?: () => void;
}) {
  const { state, user, signOut } = useLandingAuth(sessionHint);
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement | null>(null);

  const closeMenu = useCallback(() => setMenuOpen(false), []);

  useEffect(() => {
    if (!menuOpen) {
      return undefined;
    }

    function handlePointerDown(event: MouseEvent | TouchEvent) {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        setMenuOpen(false);
      }
    }

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        setMenuOpen(false);
      }
    }

    document.addEventListener('mousedown', handlePointerDown);
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('mousedown', handlePointerDown);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [menuOpen]);

  // Signing out is delegated to the canonical provider: it calls /api/auth/signout,
  // which clears the HttpOnly session cookie server-side, and clears the client session
  // state. The visitor stays on the public page, which then renders the signed-out
  // controls again. A failed request still clears local state (fail-closed).
  const handleSignOut = useCallback(async () => {
    setMenuOpen(false);
    onNavigate?.();
    await signOut();
  }, [onNavigate, signOut]);

  if (state === 'checking') {
    return (
      <div
        className={variant === 'desktop' ? styles.navRight : styles.mobileRow}
        aria-busy="true"
        data-auth-nav-state="checking"
      >
        <span className={`${styles.authPending} ${styles.authPendingWide}`} aria-hidden="true" />
        <span className={styles.authPending} aria-hidden="true" />
        <span className={styles.srOnly}>Checking your session…</span>
      </div>
    );
  }

  if (state === 'authenticated') {
    const displayName = accountDisplayName(user);

    if (variant === 'mobile') {
      return (
        <div className={styles.mobileAuth} data-auth-nav-state="authenticated">
          <p className={styles.mobileAccountIdentity}>{displayName}</p>
          <div className={styles.mobileRow}>
            <Link href={ROUTES.dashboard} className={styles.btnPrimary} prefetch={false} onClick={onNavigate}>
              Dashboard
            </Link>
            <button type="button" className={styles.btnSecondary} onClick={() => void handleSignOut()}>
              Sign out
            </button>
          </div>
        </div>
      );
    }

    return (
      <div className={styles.navRight} data-auth-nav-state="authenticated">
        <Link
          href={ROUTES.dashboard}
          className={`${styles.btnPrimary} ${styles.headerCta}`}
          prefetch={false}
          onClick={onNavigate}
        >
          Dashboard
        </Link>

        <div className={styles.accountMenu} ref={menuRef}>
          <button
            type="button"
            className={styles.accountTrigger}
            aria-haspopup="menu"
            aria-expanded={menuOpen}
            aria-label={menuOpen ? 'Close account menu' : 'Open account menu'}
            onClick={() => setMenuOpen((open) => !open)}
          >
            <span className={styles.accountAvatar} aria-hidden="true">{accountInitials(user)}</span>
          </button>

          {menuOpen ? (
            <div className={styles.accountPanel} role="menu">
              <p className={styles.accountIdentity}>{displayName}</p>
              {user?.email && user.email !== displayName ? (
                <p className={styles.accountIdentitySub}>{user.email}</p>
              ) : null}
              <Link
                href={ROUTES.dashboard}
                className={styles.accountItem}
                role="menuitem"
                prefetch={false}
                onClick={() => {
                  closeMenu();
                  onNavigate?.();
                }}
              >
                Dashboard
              </Link>
              <button
                type="button"
                className={styles.accountItem}
                role="menuitem"
                onClick={() => void handleSignOut()}
              >
                Sign out
              </button>
            </div>
          ) : null}
        </div>
      </div>
    );
  }

  if (variant === 'mobile') {
    return (
      <div className={styles.mobileRow} data-auth-nav-state="anonymous">
        <Link href={ROUTES.signIn} className={styles.btnSecondary} prefetch={false} onClick={onNavigate}>
          Sign in
        </Link>
        <Link href={ROUTES.startMonitoring} className={styles.btnPrimary} prefetch={false} onClick={onNavigate}>
          Start monitoring
        </Link>
      </div>
    );
  }

  return (
    <div className={styles.navRight} data-auth-nav-state="anonymous">
      <Link href={ROUTES.signIn} className={styles.signIn} prefetch={false}>
        Sign in
      </Link>
      <Link href={ROUTES.startMonitoring} className={`${styles.btnPrimary} ${styles.headerCta}`} prefetch={false}>
        Start monitoring
      </Link>
    </div>
  );
}
