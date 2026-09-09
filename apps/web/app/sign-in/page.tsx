import { cookies } from 'next/headers';

import PreviewDeploymentNotice from '../preview-deployment-notice';
import { getRuntimeConfig } from '../runtime-config';
import { resolveInvitationToken } from '../signup-access';
import SignInPageClient from './sign-in-page-client';

export const dynamic = 'force-dynamic';

type SignInPageProps = {
  searchParams?: any;
};

export default async function SignInPage({ searchParams }: SignInPageProps) {
  const isPreviewDeployment = process.env.VERCEL_ENV === 'preview';
  const runtimeConfig = getRuntimeConfig();
  const cookieStore = await cookies();
  const token = cookieStore.get('decoda_session')?.value;
  const params = (searchParams ?? {}) as Record<string, string | string[] | undefined>;
  const firstValue = (name: string) => {
    const value = params[name];
    return Array.isArray(value) ? (value[0] ?? null) : (value ?? null);
  };
  const nextPath = firstValue('next') ?? undefined;
  // An approved applicant may reach /sign-in from the invitation email, from the
  // accept page, or from the "Already have an account?" link on invitation-aware
  // signup. All three carry the invitation; this reads it from any of them so the
  // screen can say what the sign-in is FOR and finish the acceptance afterwards.
  const invitationToken = resolveInvitationToken({ get: firstValue }) || undefined;

  if (process.env.NODE_ENV !== 'production') {
    console.debug('[dashboard-page-data trace] source=sign-in-server-render', {
      route: '/sign-in',
      hasToken: Boolean(token),
      hasNextPath: Boolean(nextPath),
      liveModeEnabled: runtimeConfig.liveModeEnabled,
      configured: runtimeConfig.configured,
    });
  }

  if (runtimeConfig.liveModeEnabled && token) {
    if (process.env.NODE_ENV !== 'production') {
      console.debug('[dashboard-page-data trace] source=sign-in-server-redirect', {
        redirectTo: '/dashboard',
        reason: 'token-cookie-present',
        hasNextPath: Boolean(nextPath),
      });
    }
    // Avoid server-side redirect loops when a stale token cookie exists; the client auth restore flow
    // handles post-auth navigation after session validity is confirmed.
  }

  return (
    <SignInPageClient
      nextPath={nextPath}
      invitationToken={invitationToken}
      previewNotice={isPreviewDeployment ? <PreviewDeploymentNotice /> : null}
    />
  );
}
