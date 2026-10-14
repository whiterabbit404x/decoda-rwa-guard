import type { Metadata } from 'next';

/**
 * Metadata for a PUBLIC page: title, description, canonical URL and matching
 * Open Graph / Twitter cards — the same shape the company website's
 * `pageMetadata` produces (decoda-website lib/site/metadata.ts).
 *
 * Next merges metadata shallowly per key: a page's openGraph object REPLACES
 * the one inherited from the root layout — including the og:image the file
 * convention (app/opengraph-image.png) puts there. So every public page names
 * the share image explicitly; the file convention still serves it at
 * /opengraph-image.png. Relative URLs resolve against `metadataBase`, set once
 * in the root layout to the canonical Guard origin.
 */

export const PUBLIC_SITE_NAME = 'Decoda Security';

/** The share card: app/opengraph-image.png, alt text as in opengraph-image.alt.txt. */
export const SHARE_IMAGE = {
  url: '/opengraph-image.png',
  width: 1200,
  height: 630,
  type: 'image/png',
  alt: 'Decoda RWA Guard: protect the infrastructure behind tokenized finance. Detect threats, investigate with evidence, respond under policy.',
};

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
      images: [SHARE_IMAGE],
    },
    twitter: {
      card: 'summary_large_image',
      title: cardTitle,
      description,
      images: [{ url: SHARE_IMAGE.url, alt: SHARE_IMAGE.alt }],
    },
  };
}
