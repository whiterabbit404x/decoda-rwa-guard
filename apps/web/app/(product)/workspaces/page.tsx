import { Suspense } from 'react';

import WorkspacesPageClient from './workspaces-page-client';

function WorkspacesPageLoading() {
  return (
    <main className="container authPage">
      <div className="hero">
        <div>
          <p className="eyebrow">Company workspaces</p>
          <h1>Select your active workspace</h1>
          <p className="lede">Loading workspace options…</p>
        </div>
      </div>
    </main>
  );
}

export default function WorkspacesPage() {
  return (
    <Suspense fallback={<WorkspacesPageLoading />}>
      <WorkspacesPageClient />
    </Suspense>
  );
}
