import ExternalWatchlistDetailClient from './external-watchlist-detail-client';

export const dynamic = 'force-dynamic';

export const metadata = {
  title: 'External protocol · Decoda internal',
  robots: { index: false, follow: false },
};

/**
 * One externally watched protocol (founder console). Every read and action is
 * authorized by the backend; a customer receives 403 and no data.
 */
export default async function ExternalWatchlistDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <ExternalWatchlistDetailClient watchlistId={id} />;
}
