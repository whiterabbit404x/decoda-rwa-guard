import { HomeIcon } from './home-icons';
import { enterpriseStats } from './home-data';
import styles from './home.module.css';

/**
 * The hero's right-hand panel. This is a positioning panel — the three
 * figures describe how Decoda approaches security (a 24/7 monitoring
 * mindset; policy/posture/response pillars; one live flagship product).
 * They are deliberately not framed as live telemetry or customer data.
 */
export function EnterpriseSecurityPanel() {
  return (
    <aside className={styles.esPanel} aria-label="Enterprise security by design">
      <p className={styles.esHead}>Enterprise security by design</p>

      <div className={styles.esStats}>
        {enterpriseStats.map((stat) => (
          <div key={stat.label} className={styles.esStat}>
            <span className={styles.esIcon}>
              <HomeIcon name={stat.icon} />
            </span>
            <span className={styles.esValue}>{stat.value}</span>
            <span className={styles.esLabel}>{stat.label}</span>
          </div>
        ))}
      </div>

      {/* Restrained blockchain / network infrastructure motif (decorative). */}
      <div className={styles.esVisual} aria-hidden="true">
        <svg viewBox="0 0 360 150" fill="none" role="presentation" className={styles.esGrid}>
          <defs>
            <linearGradient id="esBeam" x1="180" y1="150" x2="180" y2="30" gradientUnits="userSpaceOnUse">
              <stop offset="0" stopColor="#3b82f6" stopOpacity="0.35" />
              <stop offset="1" stopColor="#3b82f6" stopOpacity="0" />
            </linearGradient>
          </defs>
          {/* perspective grid */}
          <path d="M20 138 L150 60 M110 138 L168 60 M200 138 L192 60 M290 138 L216 60 M340 138 L232 60" stroke="rgba(96,165,250,0.16)" strokeWidth="1" />
          <path d="M40 118 H320 M64 96 H296 M92 74 H268" stroke="rgba(96,165,250,0.12)" strokeWidth="1" />
          {/* upward light beam from the chip */}
          <path d="M150 92 L210 92 L200 30 L160 30 Z" fill="url(#esBeam)" />
          {/* chip / node */}
          <rect x="160" y="76" width="40" height="30" rx="6" fill="rgba(59,130,246,0.18)" stroke="#3b82f6" strokeWidth="1.4" />
          <path d="M180 84l6 4-6 4-6-4 6-4z" fill="#6aa9ff" />
          <path d="M160 82h-12M160 92h-12M160 100h-10M200 82h12M200 92h12M200 100h10M180 76v-10M172 76v-7M188 76v-7" stroke="rgba(96,165,250,0.5)" strokeWidth="1.2" />
          {/* nodes */}
          <circle cx="148" cy="82" r="2.4" fill="#6aa9ff" />
          <circle cx="212" cy="82" r="2.4" fill="#6aa9ff" />
          <circle cx="150" cy="100" r="2.4" fill="#38bdf8" />
          <circle cx="210" cy="100" r="2.4" fill="#38bdf8" />
          <circle cx="180" cy="66" r="2.4" fill="#6aa9ff" />
        </svg>
      </div>
    </aside>
  );
}
