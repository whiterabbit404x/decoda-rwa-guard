import Link from 'next/link';

import { DECODA_SITE } from '../../public-site-links';
import { DecodaLogo } from './decoda-logo';
import { ECOSYSTEM, ROUTES } from './home-data';
import { HomeIcon } from './home-icons';
import styles from './site.module.css';

type FooterLink = { label: string; href: string; external?: boolean };

// Live Proof is intentionally omitted from public navigation until genuine
// public verification artifacts are published to the deployment. The page
// still exists at /live-proof in a truthful, graceful state.
const PRODUCT_LINKS: FooterLink[] = [
  { label: 'Platform', href: ROUTES.platformAnchor },
  { label: 'How it works', href: ROUTES.howItWorksAnchor },
  { label: 'Product tour', href: ROUTES.productAnchor },
  { label: 'Pricing', href: ROUTES.pricing },
  { label: 'Request a pilot', href: ROUTES.requestPilot },
  { label: 'Sign in', href: ROUTES.signIn },
];

const TRUST_LINKS: FooterLink[] = [
  { label: 'Security & Trust', href: ROUTES.trust },
  { label: 'Security', href: ROUTES.security },
  { label: 'Support', href: ROUTES.support },
  { label: 'Privacy Policy', href: ROUTES.privacy },
  { label: 'Terms of Service', href: ROUTES.terms },
];

const COMPANY_LINKS: FooterLink[] = [
  { label: 'Decoda Security', href: DECODA_SITE.website, external: true },
  { label: 'Decoda ecosystem', href: DECODA_SITE.ecosystem, external: true },
  { label: 'Contact', href: DECODA_SITE.contact, external: true },
];

function FooterAnchor({ link }: { link: FooterLink }) {
  if (link.external) {
    return (
      <a href={link.href}>
        {link.label}
        <HomeIcon name="arrowUpRight" size={13} />
      </a>
    );
  }
  // In-page anchors and the sign-in/pilot routes are plain links: /request-pilot
  // may hand off to the company website, which a client-side transition would
  // only delay.
  if (link.href.includes('#') || link.href === ROUTES.requestPilot || link.href === ROUTES.signIn) {
    return <a href={link.href}>{link.label}</a>;
  }
  return (
    <Link href={link.href} prefetch={false}>
      {link.label}
    </Link>
  );
}

export function MarketingFooter({ supportEmail }: { supportEmail: string }) {
  const year = new Date().getFullYear();

  return (
    <footer className={styles.footer}>
      <div className={styles.footerInner}>
        <div className={styles.footerTop}>
          <div className={styles.footerBrand}>
            <Link href="/" className={styles.brand} prefetch={false} aria-label="Decoda RWA Guard — home">
              <DecodaLogo />
            </Link>
            <p>
              Security monitoring, evidence-grounded investigation and policy-controlled response for
              tokenized financial infrastructure.
            </p>
            <ul className={styles.footerStatus} aria-label="Decoda product status">
              {ECOSYSTEM.map((product) => (
                <li key={product.key}>
                  <span className={styles.footerDot} data-tone={product.tone} aria-hidden="true" />
                  <span>
                    <strong>{product.shortName}</strong> — {product.status}
                  </span>
                </li>
              ))}
            </ul>
          </div>

          <nav className={styles.footerCol} aria-label="Product">
            <h2>Product</h2>
            <ul>
              {PRODUCT_LINKS.map((link) => (
                <li key={link.label}>
                  <FooterAnchor link={link} />
                </li>
              ))}
            </ul>
          </nav>

          <nav className={styles.footerCol} aria-label="Trust and legal">
            <h2>Trust &amp; legal</h2>
            <ul>
              {TRUST_LINKS.map((link) => (
                <li key={link.label}>
                  <FooterAnchor link={link} />
                </li>
              ))}
            </ul>
          </nav>

          <nav className={styles.footerCol} aria-label="Company">
            <h2>Company</h2>
            <ul>
              {COMPANY_LINKS.map((link) => (
                <li key={link.label}>
                  <FooterAnchor link={link} />
                </li>
              ))}
              <li>
                <a href={`mailto:${supportEmail}`}>{supportEmail}</a>
              </li>
            </ul>
          </nav>
        </div>

        <div className={styles.footerBottom}>
          <p>© {year} Decoda Security. All rights reserved.</p>
          <p>Product interfaces shown on this site are illustrations with sample data unless stated otherwise.</p>
        </div>
      </div>
    </footer>
  );
}
