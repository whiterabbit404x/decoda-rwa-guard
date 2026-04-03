import { cookies } from 'next/headers';

import PreviewDeploymentNotice from '../preview-deployment-notice';
import { getRuntimeConfig } from '../runtime-config';
import SignInPageClient from './sign-in-page-client';

export const dynamic = 'force-dynamic';

type SignInPageProps = {
  searchParams?: Promise<{ next?: string | string[] | undefined }>;
};

export default async function SignInPage({ searchParams }: SignInPageProps) {
  const isPreviewDeployment = process.env.VERCEL_ENV === 'preview';
  const runtimeConfig = getRuntimeConfig();
  const cookieStore = await cookies();
  const token = cookieStore.get('decoda-pilot-access-token')?.value;
  const resolvedSearchParams = await searchParams;
  const nextParam = resolvedSearchParams?.next;
  const nextPath = Array.isArray(nextParam) ? nextParam[0] : nextParam;

  if (runtimeConfig.liveModeEnabled && token) {
    console.debug('[dashboard-page-data trace] source=sign-in-server-redirect', {
      redirectTo: '/dashboard',
      reason: 'token-cookie-present',
      hasNextPath: Boolean(nextPath),
    });
    // Avoid server-side redirect loops when a stale token cookie exists; the client auth restore flow
    // handles post-auth navigation after session validity is confirmed.
  }

  return <SignInPageClient nextPath={nextPath} previewNotice={isPreviewDeployment ? <PreviewDeploymentNotice /> : null} />;
}
