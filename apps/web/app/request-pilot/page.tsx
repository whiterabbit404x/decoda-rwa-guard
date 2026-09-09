import Link from 'next/link';

import RequestPilotClient from './request-pilot-client';

export const dynamic = 'force-dynamic';

export const metadata = {
  title: 'Request a Pilot evaluation · Decoda RWA Guard',
  description:
    'Apply for a Decoda RWA Guard Pilot evaluation. Every Pilot evaluation is reviewed and approved by Decoda before it is activated.',
};

function SmallShield() {
  return (
    <svg width="22" height="24" viewBox="0 0 26 28" fill="none" aria-hidden="true">
      <path d="M13 1.5L2 6.5V14c0 6.2 4.8 11.5 11 12.5 6.2-1 11-6.3 11-12.5V6.5L13 1.5z" fill="#3b82f6" />
      <path d="M9 14.5l2.5 2.5 5.5-5.5" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

/**
 * Public Pilot application.
 *
 * This page is where "Request Pilot" leads, and submitting it grants nothing:
 * it records a PENDING application that Decoda internal staff review. No
 * workspace, organization, plan, entitlement, or monitoring exists until an
 * approved applicant accepts the invitation mailed to their address.
 *
 * The copy says exactly that. Promising instant access here and then not
 * delivering it would be the same untruth as a dashboard that renders "healthy"
 * with no data behind it.
 */
export default function RequestPilotPage() {
  return (
    <>
      <a href="#request-pilot-main" className="skipToContent">Skip to main content</a>
      <header className="mktStandaloneNav" role="banner">
        <div className="mktStandaloneNavInner">
          <Link href="/" className="mktStandaloneNavLogo" prefetch={false}>
            <SmallShield />
            <span className="mktNavLogoText">
              <span className="mktStandaloneNavBrand">DECODA</span>
              <span className="mktStandaloneNavProduct">RWA GUARD</span>
            </span>
          </Link>
          <nav className="mktStandaloneNavLinks" aria-label="Site navigation">
            <Link href="/#platform" className="mktStandaloneNavLink" prefetch={false}>Product</Link>
            <Link href="/pricing" className="mktStandaloneNavLink" prefetch={false}>Pricing</Link>
            <Link href="/trust" className="mktStandaloneNavLink" prefetch={false}>Trust</Link>
          </nav>
          <div className="mktStandaloneNavRight">
            <Link href="/sign-in" className="mktStandaloneNavSignIn" prefetch={false}>Sign in</Link>
          </div>
        </div>
      </header>
      <RequestPilotClient />
    </>
  );
}
