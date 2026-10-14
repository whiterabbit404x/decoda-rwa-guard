import type { Metadata, Viewport } from 'next';
import { redirect } from 'next/navigation';

import { decodaLinks, guardIdentityMode } from '../decoda-identity';
import { publicPageMetadata } from '../public-metadata';

import { RequestPilotView } from './request-pilot-view';

export const dynamic = 'force-dynamic';

export const metadata: Metadata = publicPageMetadata({
  title: 'Request a Pilot evaluation',
  description:
    'Apply for a Decoda RWA Guard Pilot evaluation. Every Pilot evaluation is reviewed and approved by Decoda before it is activated.',
  path: '/request-pilot',
});

export const viewport: Viewport = { themeColor: '#ffffff', colorScheme: 'light' };

/**
 * Public Pilot application.
 *
 * This page is where "Request Pilot" leads, and submitting it grants nothing:
 * it records a PENDING application that Decoda internal staff review. No
 * workspace, organization, plan, entitlement, or monitoring exists until an
 * approved applicant accepts the invitation mailed to their address.
 *
 * The copy says exactly that. Promising instant access here and then not
 * delivering it would be the same untruth as a dashboard that renders "healthy"
 * with no data behind it.
 */
export default function RequestPilotPage() {
  if (guardIdentityMode() !== 'legacy') {
    // Shared Decoda identity: Pilot access is requested on the Decoda website,
    // which owns review and invitations (the Guard API refuses this intake too).
    redirect(decodaLinks().requestAccess);
  }
  return <RequestPilotView />;
}
