import { cookies } from 'next/headers';
import { redirect } from 'next/navigation';

import PreviewDeploymentNotice from '../preview-deployment-notice';
import { getRuntimeConfig } from '../runtime-config';
import SignInPageClient from './sign-in-page-client';

export const dynamic = 'force-dynamic';

export default async function SignInPage({
  searchParams,
}: {
  searchParams?: Promise<{ next?: string | string[] }>;
}) {
  const isPreviewDeployment = process.env.VERCEL_ENV === 'preview';
  const runtimeConfig = getRuntimeConfig();
  const token = (await cookies()).get('decoda-pilot-access-token')?.value;
  const resolvedSearchParams = searchParams ? await searchParams : undefined;
  const nextParam = resolvedSearchParams?.next;
  const nextPath = Array.isArray(nextParam) ? nextParam[0] : nextParam;

  if (runtimeConfig.liveModeEnabled && token) {
    redirect('/dashboard');
  }

  return <SignInPageClient nextPath={nextPath} previewNotice={isPreviewDeployment ? <PreviewDeploymentNotice /> : null} />;
}
