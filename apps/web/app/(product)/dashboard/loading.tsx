/**
 * Route-level loading state for /dashboard.
 *
 * Mirrors the executive summary's own skeleton (same classes, same grid), so
 * the page does not jump when the client component takes over. Every size is
 * a class in styles.css: this is a server component, and the production CSP
 * (`style-src 'self' 'nonce-…'`) drops inline style attributes, which left the
 * previous skeleton blocks unsized in production.
 *
 * It renders placeholders only — no number, status or label that could be
 * mistaken for workspace data.
 */
export default function DashboardLoading() {
  return (
    <main className="container productPage dashboardExecPage" aria-busy="true" aria-live="polite">
      <div className="dashboardPageHeader dashboardExecHeader">
        <div>
          <h1 className="dashboardPageTitle">Dashboard</h1>
          <p className="dashboardPageSubtitle">Loading the executive summary for this workspace…</p>
        </div>
      </div>

      <div className="execSkeleton" aria-hidden="true">
        <div className="dcPosture">
          {Array.from({ length: 4 }).map((_, index) => (
            <div key={index} className="dcPostureItem execSkeletonCard dcSkeletonPosture" />
          ))}
        </div>
        <div className="execMetricRow execMetricRowScreen2">
          <div className="execBriefCard dataCard execSkeletonCard execSkeletonBrief" />
          <div className="execMetricCluster">
            {Array.from({ length: 5 }).map((_, index) => (
              <div key={index} className="execMetricCard dataCard execSkeletonCard" />
            ))}
          </div>
        </div>
        <div className="execMainGrid execMainGridScreen2">
          <div className="execSectionCard dataCard execSkeletonCard execSkeletonChart" />
          <div className="execSectionCard dataCard execSkeletonCard execSkeletonPanel" />
        </div>
      </div>
    </main>
  );
}
