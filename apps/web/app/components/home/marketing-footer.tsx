import Link from 'next/link';

import { HomeIcon } from './home-icons';
import { BrandLockup } from './marketing-header';
import { footerColumns, ROUTES } from './home-data';
import styles from './home.module.css';

function FooterLink({ href, label }: { href: string; label: string }) {
  if (href.startsWith('#') || href.startsWith('mailto:')) {
    return (
      <a href={href} className={styles.footerLink}>
        {label}
      </a>
    );
  }
  return (
    <Link href={href} className={styles.footerLink} prefetch={false}>
      {label}
    </Link>
  );
}

export function MarketingFooter() {
  return (
    <footer className={styles.footer}>
      <div className={styles.footerInner}>
        <div className={styles.footerBrandCol}>
          <Link href="/" className={styles.brand} prefetch={false} aria-label="Decoda Security home">
            <BrandLockup />
          </Link>
          <p className={styles.footerTag}>
            Security infrastructure for blockchain financial systems. Built for institutions.
            Designed for trust at scale.
          </p>
        </div>

        {footerColumns.map((col) => (
          <nav key={col.head} className={styles.footerCol} aria-label={col.head}>
            <span className={styles.footerColHead}>{col.head}</span>
            {col.links.map((link) => (
              <FooterLink key={link.label} href={link.href} label={link.label} />
            ))}
          </nav>
        ))}

        <div className={styles.footerCta}>
          <span className={styles.footerColHead}>Ready to get started?</span>
          <p className={styles.footerCtaText}>
            Talk to our team about securing your blockchain financial programs.
          </p>
          <a href={ROUTES.demoMailto} className={`${styles.btnPrimary} ${styles.footerCtaBtn}`}>
            Request a strategy session
            <HomeIcon name="arrowRight" className={styles.btnArrow} />
          </a>
        </div>
      </div>

      <div className={styles.footerBottom}>
        <span className={styles.footerCopy}>&copy; 2026 Decoda Security. All rights reserved.</span>
        <span className={styles.footerCopy}>Security infrastructure for blockchain financial systems.</span>
      </div>
    </footer>
  );
}
