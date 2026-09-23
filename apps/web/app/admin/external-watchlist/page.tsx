import ExternalWatchlistClient from './external-watchlist-client';

export const dynamic = 'force-dynamic';

export const metadata = {
  title: 'External Watchlist · Decoda internal',
  // Internal founder console: never indexed, never linked from the product.
  robots: { index: false, follow: false },
};

/**
 * Founder-only External Watchlist.
 *
 * Outside the (product) route group, like /admin/customers, so it never
 * appears in the customer app shell. Authorization is the backend's on every
 * request (internal admin, then EXTERNAL_WATCHLIST_ENABLED); a customer who
 * opens this URL receives 403 and sees no watchlist data.
 */
export default function ExternalWatchlistPage() {
  return <ExternalWatchlistClient />;
}
