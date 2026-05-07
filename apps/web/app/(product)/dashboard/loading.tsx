import type { CSSProperties } from 'react';

type SkeletonBlockProps = {
  className?: string;
  style?: CSSProperties;
};

function SkeletonBlock({ className = '', style }: SkeletonBlockProps) {
  return (
    <div
      className={["dashboardLoadingSkeleton", className].filter(Boolean).join(' ')}
      style={style}
      aria-hidden="true"
    />
  );
}

export default function DashboardLoading() {
  return (
    <main className="container productPage" aria-busy="true" aria-live="polite">
      <section className="hero">
        <div>
          <p className="eyebrow">Loading workspace</p>
          <h1>Dashboard</h1>
          <SkeletonBlock className="dashboardLoadingLine" style={{ width: '90%' }} />
          <SkeletonBlock className="dashboardLoadingLine" style={{ width: '70%' }} />
          <div className="heroActionRow">
            <SkeletonBlock style={{ height: '2rem', width: '7rem' }} />
            <SkeletonBlock style={{ height: '2rem', width: '8rem' }} />
            <SkeletonBlock style={{ height: '2rem', width: '9rem' }} />
          </div>
        </div>
        <div className="heroPanel">
          <SkeletonBlock className="dashboardLoadingLine" style={{ width: '100%' }} />
          <SkeletonBlock className="dashboardLoadingLine" style={{ width: '85%' }} />
          <SkeletonBlock className="dashboardLoadingLine" style={{ width: '75%' }} />
          <SkeletonBlock className="dashboardLoadingLine" style={{ width: '80%' }} />
        </div>
      </section>

      <section className="summaryGrid" aria-label="Loading dashboard summary cards">
        {Array.from({ length: 6 }).map((_, index) => (
          <article key={index} className="metricCard">
            <SkeletonBlock className="dashboardLoadingLine" style={{ width: '50%' }} />
            <SkeletonBlock style={{ height: '2rem', width: '33%' }} />
            <SkeletonBlock className="dashboardLoadingLine" style={{ width: '66%' }} />
          </article>
        ))}
      </section>

      <section className="featureSection" aria-label="Loading dashboard sections">
        <div className="sectionHeader">
          <div>
            <p className="eyebrow">Loading section</p>
            <h2>Workspace activity</h2>
          </div>
        </div>
        <div className="threeColumnSection">
          {Array.from({ length: 3 }).map((_, columnIndex) => (
            <div key={columnIndex} className="stack compactStack">
              {Array.from({ length: 2 }).map((__, cardIndex) => (
                <article key={cardIndex} className="dataCard">
                  <SkeletonBlock style={{ height: '1.25rem', width: '66%' }} />
                  <SkeletonBlock className="dashboardLoadingLine" style={{ width: '100%' }} />
                  <SkeletonBlock className="dashboardLoadingLine" style={{ width: '85%' }} />
                </article>
              ))}
            </div>
          ))}
        </div>
      </section>
    </main>
  );
}
