"use client";

import { ScreenError } from '../../components/screen-states';

type DashboardErrorBoundaryProps = {
  error: Error & { digest?: string };
  reset: () => void;
};

/**
 * The dashboard's own failure state. It used utility classes from a CSS
 * framework this app does not ship, so it rendered as unstyled text; it now
 * shares the product's screen-level failure state. "Retry" re-renders the
 * screen in place; "Hard reload" reloads the document, for a stale bundle.
 */
export default function DashboardErrorBoundary({ error, reset }: DashboardErrorBoundaryProps) {
  const hardReloadDashboard = () => {
    window.location.assign('/dashboard');
  };

  return (
    <ScreenError
      title="Dashboard unavailable"
      digest={error?.digest}
      actions={(
        <>
          <button type="button" className="btn btn-primary" onClick={reset}>
            Retry
          </button>
          <button type="button" className="btn btn-secondary" onClick={hardReloadDashboard}>
            Hard reload
          </button>
        </>
      )}
    />
  );
}
