'use client';

import Link from 'next/link';

import type { LandingSessionHint } from '../../auth-guards';
import { resolveStartMonitoringTarget } from './auth-nav-state';
import { ROUTES } from './home-data';
import { HomeIcon } from './home-icons';
import { useLandingAuth } from './use-landing-auth';
import styles from './home.module.css';

/**
 * The primary conversion CTA. The label never changes — only the destination:
 * a signed-out visitor keeps the existing /sign-up onboarding flow, and an
 * authenticated visitor goes straight to /dashboard instead of being asked to
 * authenticate again.
 */
export function StartMonitoringCta({
  sessionHint,
  withArrow = false,
}: {
  sessionHint: LandingSessionHint;
  withArrow?: boolean;
}) {
  const { state } = useLandingAuth(sessionHint);
  const href = resolveStartMonitoringTarget(state) === 'dashboard' ? ROUTES.dashboard : ROUTES.startMonitoring;

  return (
    <Link href={href} className={styles.btnPrimary} prefetch={false}>
      Start monitoring
      {withArrow ? <HomeIcon name="arrowRight" className={styles.btnArrow} /> : null}
    </Link>
  );
}
