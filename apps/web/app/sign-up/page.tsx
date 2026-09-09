import { cookies } from 'next/headers';
import { redirect } from 'next/navigation';
import { Suspense } from 'react';

import PreviewDeploymentNotice from '../preview-deployment-notice';
import { getRuntimeConfig } from '../runtime-config';
import { acceptInvitationPath, resolveInvitationToken } from '../signup-access';
import SignUpPageClient from './sign-up-page-client';

export const dynamic = 'force-dynamic';

/**
 * Public signup.
 *
 * Reaching this route is not approval. The page offers account creation only to
 * someone holding an invitation the BACKEND still recognises; everyone else sees
 * the approval-only state and a link to the Pilot application. Signing up has
 * never created a tenant since the approval-only change (services/api/app/pilot.py
 * :signup_user) — this page stops implying that it does, and stops collecting a
 * workspace name that would name nothing.
 */
export default async function SignUpPage({
  searchParams,
}: {
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
}) {
  const isPreviewDeployment = process.env.VERCEL_ENV === 'preview';
  const runtimeConfig = getRuntimeConfig();
  const cookieStore = await cookies();
  const token = cookieStore.get('decoda_session')?.value;
  const resolvedSearchParams = (await searchParams) ?? {};
  const firstValue = (name: string) => {
    const value = resolvedSearchParams[name];
    return Array.isArray(value) ? (value[0] ?? null) : (value ?? null);
  };
  const invitationToken = resolveInvitationToken({ get: firstValue });

  if (runtimeConfig.liveModeEnabled && token) {
    // Already signed in. With an invitation in hand there is nothing to create
    // here, so go straight to the one activation path rather than to a signup
    // form the session has already outgrown.
    redirect(invitationToken ? acceptInvitationPath(invitationToken) : '/dashboard');
  }

  return (
    <Suspense fallback={null}>
      <SignUpPageClient previewNotice={isPreviewDeployment ? <PreviewDeploymentNotice /> : null} />
    </Suspense>
  );
}
