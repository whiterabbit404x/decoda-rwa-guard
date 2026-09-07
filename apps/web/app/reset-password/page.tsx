import { Suspense } from 'react';

import ResetPasswordClient from './reset-password-client';

export const dynamic = 'force-dynamic';
export const fetchCache = 'force-no-store';

// The client reads the URL to decide which recovery step to show, so this fallback
// covers only the moment before that decision — it deliberately renders no form.
function PageLoadingState() {
  return (
    <div className="siPage">
      <div className="siWrapper">
        <div className="siContainer">
          <main className="siOuter">
            <section className="siFormPanel" aria-label="Account recovery">
              <div className="rpCentered" role="status" aria-busy="true">
                <span className="rpSpinner" aria-hidden="true" />
                <h1 className="siFormTitle">Account recovery</h1>
                <p className="siFormSubtitle">Loading...</p>
              </div>
            </section>
          </main>
        </div>
      </div>
    </div>
  );
}

export default function ResetPasswordPage() {
  return (
    <Suspense fallback={<PageLoadingState />}>
      <ResetPasswordClient />
    </Suspense>
  );
}
