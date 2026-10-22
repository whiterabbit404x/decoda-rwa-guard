import Link from 'next/link';

/**
 * Screen-level loading and failure states, shared by the route boundaries
 * (`(product)/loading.tsx`, `(product)/error.tsx`) and the auth gate, so a
 * screen moves from "loading" to "loaded" in one shape instead of three.
 *
 * Both are deliberately content-free. The skeleton is placeholders only — no
 * number, status or label that could be read as workspace data, and nothing
 * coloured as healthy. The failure state reports that the screen failed; it
 * never substitutes an empty or "all clear" view for it.
 *
 * Server-safe (no hooks, no 'use client'): every size is a class in styles.css,
 * because the production CSP drops inline style attributes.
 */

/**
 * Deliberately small markup: one labelled status and eight bare blocks that
 * styles.css lays out (title, line, four figures, two panels). The auth gate
 * renders this inside the product layout's Suspense boundary, and React streams
 * a completed boundary inline only while its HTML stays under 500 bytes; past
 * that, the server outlines it (fallback first, content swapped in by script).
 */
export function ScreenSkeleton({ label = 'Loading…' }: { label?: string }) {
  return (
    <div className="screenSkeleton" role="status" aria-busy="true" data-testid="screen-skeleton">
      <span className="sr-only">{label}</span>
      <div className="screenSkeletonShape" aria-hidden="true">
        <span /><span /><span /><span /><span /><span /><span /><span />
      </div>
    </div>
  );
}

/**
 * An unexpected failure while rendering a screen. Nothing is retried or
 * submitted automatically and no action was taken on the operator's behalf;
 * they choose to try again. The error digest, when Next supplies one, is shown
 * so a support request can be matched to the server log.
 */
export function ScreenError({
  title = 'This screen could not be displayed',
  digest,
  onRetry,
  actions,
}: {
  title?: string;
  digest?: string;
  onRetry?: () => void;
  /** Replaces the default "Try again / Back to dashboard" controls. */
  actions?: React.ReactNode;
}) {
  return (
    <section className="screenError" role="alert" aria-labelledby="screen-error-title">
      <h2 id="screen-error-title">{title}</h2>
      <p className="muted">
        Something went wrong while showing this page. No action was taken on your behalf, and no monitoring
        status has changed because of it. Try again, or return to the dashboard.
      </p>
      {digest ? <p className="tableMeta">Reference: <code>{digest}</code></p> : null}
      <div className="buttonRow">
        {actions ?? (
          <>
            {onRetry ? (
              <button type="button" className="btn btn-primary" onClick={onRetry}>
                Try again
              </button>
            ) : null}
            <Link className="btn btn-secondary" href="/dashboard" prefetch={false}>
              Back to dashboard
            </Link>
          </>
        )}
      </div>
    </section>
  );
}
