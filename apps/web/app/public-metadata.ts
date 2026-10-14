import type { Metadata } from 'next';

/**
 * Metadata for a PUBLIC page: title, description, canonical URL and matching
 * Open Graph / Twitter cards — the same shape the company website's
 * `pageMetadata` produces (decoda-website lib/site/metadata.ts).
 *
 * Next merges metadata shallowly per key, so each page restates the whole
 * openGraph object rather than inheriting half of it. The share image itself
 * comes from the file convention (app/opengraph-image.png), which Next adds
 * to every route's og:image; X/Twitter falls back to og:image for a
 * summary_large_image card. Relative URLs resolve against `metadataBase`,
 * set once in the root layout to the canonical Guard origin.
 */

export const PUBLIC_SITE_NAME = 'Decoda Security';

type PublicMetadataInput = {
  /** The <title>. A string becomes "<title> | Decoda RWA Guard". */
  title: string | { absolute: string };
  /** The social-card title. Defaults to the full document title. */
  socialTitle?: string;
  description: string;
  /** Canonical path on the Guard origin, e.g. "/pricing". */
  path: string;
};

export function publicPageMetadata({ title, socialTitle, description, path }: PublicMetadataInput): Metadata {
  const documentTitle = typeof title === 'string' ? `${title} | Decoda RWA Guard` : title.absolute;
  const cardTitle = socialTitle ?? documentTitle;
  return {
    title: typeof title === 'string' ? { absolute: documentTitle } : title,
    description,
    alternates: { canonical: path },
    openGraph: {
      type: 'website',
      siteName: PUBLIC_SITE_NAME,
      locale: 'en_US',
      url: path,
      title: cardTitle,
      description,
    },
    twitter: {
      card: 'summary_large_image',
      title: cardTitle,
      description,
    },
  };
}
