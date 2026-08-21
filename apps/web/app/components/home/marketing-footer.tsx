import Link from 'next/link';

import { DecodaLogo } from './home-icons';
import { ROUTES } from './home-data';
import styles from './home.module.css';

// Live Proof is intentionally omitted from prominent public navigation until
// genuine public verification artifacts are published to the deployment. The
// page still exists at /live-proof in a truthful, graceful state.
const PRODUCT_LINKS = [
  { label: 'Platform', href: ROUTES.platformAnchor, external: false },
  { label: 'How it works', href: ROUTES.howItWorksAnchor, external: false },
  { label: 'RWA Security', href: ROUTES.rwaAnchor, external: false },
  { label: 'Pricing', href: '/pricing', external: false },
];

const COMPANY_LINKS = [
  { label: 'Security & Trust', href: '/trust', external: false },
  { label: 'Support', href: '/support', external: false },
  { label: 'Privacy', href: '/privacy', external: false },
  { label: 'Terms', href: '/terms', external: false },
];

export function MarketingFooter({ supportEmail }: { supportEmail: string }) {
  return (
    <footer className={styles.footer}>
      <div className={styles.footerInner}>
        <div>
          <Link href="/" className={styles.brand} prefetch={false}>
            <DecodaLogo className={styles.brandMark} />
            <span className={styles.brandName}>DECODA</span>
          </Link>
          <p className={styles.footerTag}>
            Real-time RWA security and incident-response with evidence-grounded AI and
            policy-controlled automation.
          </p>
        </div>

        <nav className={styles.footerCol} aria-label="Product">
          <span className={styles.footerColHead}>Product</span>
          {PRODUCT_LINKS.map((link) =>
            link.href.startsWith('#') ? (
              <a key={link.label} href={link.href} className={styles.footerLink}>
                {link.label}
              </a>
            ) : (
              <Link key={link.label} href={link.href} className={styles.footerLink} prefetch={false}>
                {link.label}
              </Link>
            ),
          )}
        </nav>

        <nav className={styles.footerCol} aria-label="Company">
          <span className={styles.footerColHead}>Company</span>
          {COMPANY_LINKS.map((link) => (
            <Link key={link.label} href={link.href} className={styles.footerLink} prefetch={false}>
              {link.label}
            </Link>
          ))}
          <a href={`mailto:${supportEmail}`} className={styles.footerLink}>
            {supportEmail}
          </a>
        </nav>
      </div>

      <div className={styles.footerBottom}>
        <span className={styles.footerCopy}>&copy; 2026 Decoda. All rights reserved.</span>
        <span className={styles.footerCopy}>Real-time RWA security &amp; incident response.</span>
      </div>
    </footer>
  );
}
