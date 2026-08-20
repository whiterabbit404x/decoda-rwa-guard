import {
  dashboardPreview,
  investigationPreview,
  reportingPreview,
} from './home-data';
import {
  DashboardPreviewBody,
  InvestigationPreviewBody,
  ProductPreviewCard,
  ReportingPreviewBody,
} from './product-preview-card';
import styles from './home.module.css';

export function RWASecurityPlatformSection() {
  return (
    <section className={styles.section} id="rwa-security">
      <div className={styles.sectionInner}>
        <div className={styles.sectionHeadCenter}>
          <p className={styles.eyebrow}>Flagship solution</p>
          <h2 className={styles.sectionTitle}>RWA Security Platform</h2>
          <p className={styles.sectionLead}>
            A purpose-built security operating layer for real-world asset programs — designed to
            reduce risk, strengthen controls, and provide defensible operational evidence. Decoda
            RWA Security is a real-time RWA security and incident-response platform with
            evidence-grounded AI investigation and policy-controlled automation.
          </p>
        </div>

        <div className={styles.previewGrid}>
          <ProductPreviewCard
            title="Dashboard overview"
            caption={dashboardPreview.caption}
            ariaLabel="RWA Security dashboard overview preview"
          >
            <DashboardPreviewBody />
          </ProductPreviewCard>

          <ProductPreviewCard
            title="Investigation / incident workflow"
            caption={investigationPreview.caption}
            ariaLabel="RWA Security investigation and incident workflow preview"
          >
            <InvestigationPreviewBody />
          </ProductPreviewCard>

          <ProductPreviewCard
            title="Evidence / audit trail"
            caption={reportingPreview.caption}
            ariaLabel="RWA Security evidence and audit trail preview"
          >
            <ReportingPreviewBody />
          </ProductPreviewCard>
        </div>
      </div>
    </section>
  );
}
