'use client';

import Link from 'next/link';
import { useCallback, useEffect, useRef, useState } from 'react';

import type { LandingSessionHint } from '../../auth-guards';
import { accountDisplayName, accountInitials } from './auth-nav-state';
import { ROUTES } from './home-data';
import { HomeIcon } from './home-icons';
import { useLandingAuth } from './use-landing-auth';
import styles from './site.module.css';

export type AuthNavVariant = 'desktop' | 'mobile';

/**
 * Auth-aware controls for the public site header.
 *
 * Signed out  → "Sign in" + "Request Pilot" — the same two ways into Decoda
 *               the company website offers. There is no registration: Decoda
 *               accounts are created by invitation, and /request-pilot is the
 *               reviewed application (or, with the shared Decoda identity,
 *               the company website's Request pilot page).
 * Signed in   → "Dashboard" + an account menu (name/email, Dashboard, Sign out).
 * Unresolved  → a neutral placeholder, so the signed-out controls never flash
 *               for a visitor who is in fact signed in.
 *
 * The authenticated visitor is NOT redirected away from the public page; only
 * these controls change.
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
    const next = await signOut();
    // Shared Decoda identity: continue to the WorkOS logout URL so the Decoda
    // session ends as well; otherwise stay on this page, signed out.
    if (next) window.location.href = next;
  }, [onNavigate, signOut]);

  if (state === 'checking') {
    return (
      <div
        className={variant === 'desktop' ? styles.actions : styles.menuActions}
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
        <div className={styles.menuActions} data-auth-nav-state="authenticated">
          <p className={styles.mobileAccountIdentity}>{displayName}</p>
          <Link href={ROUTES.dashboard} className={styles.btnPrimary} prefetch={false} onClick={onNavigate}>
            Dashboard
          </Link>
          <button type="button" className={styles.btnSecondary} onClick={() => void handleSignOut()}>
            Sign out
          </button>
        </div>
      );
    }

    return (
      <div className={styles.actions} data-auth-nav-state="authenticated">
        <Link
          href={ROUTES.dashboard}
          className={`${styles.btnPrimary} ${styles.headerCta}`}
          prefetch={false}
          onClick={onNavigate}
        >
          Dashboard
          <HomeIcon name="arrowRight" size={16} />
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
      <div className={styles.menuActions} data-auth-nav-state="anonymous">
        <Link href={ROUTES.signIn} className={styles.btnSecondary} prefetch={false} onClick={onNavigate}>
          Sign in
        </Link>
        <Link href={ROUTES.requestPilot} className={styles.btnPrimary} prefetch={false} onClick={onNavigate}>
          Request Pilot
          <HomeIcon name="arrowRight" size={16} />
        </Link>
      </div>
    );
  }

  return (
    <div className={styles.actions} data-auth-nav-state="anonymous">
      <Link href={ROUTES.signIn} className={styles.signIn} prefetch={false}>
        Sign in
      </Link>
      <Link href={ROUTES.requestPilot} className={`${styles.btnPrimary} ${styles.headerCta}`} prefetch={false}>
        Request Pilot
        <HomeIcon name="arrowRight" size={16} />
      </Link>
    </div>
  );
}
