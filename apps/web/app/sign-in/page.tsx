import { cookies } from 'next/headers';
import { redirect } from 'next/navigation';

import PreviewDeploymentNotice from '../preview-deployment-notice';
import { getRuntimeConfig } from '../runtime-config';
import SignInPageClient from './sign-in-page-client';

export const dynamic = 'force-dynamic';

export default async function SignInPage({
  searchParams,
}: {
  searchParams?: Promise<{ next?: string }>;
}) {
  const isPreviewDeployment = process.env.VERCEL_ENV === 'preview';
  const runtimeConfig = getRuntimeConfig();
  const cookieStore = await cookies();
  const token = cookieStore.get('decoda-pilot-access-token')?.value;
  const resolvedSearchParams = searchParams ? await searchParams : undefined;

  if (runtimeConfig.liveModeEnabled && token) {
    redirect('/dashboard');
  }

  return (
    <SignInPageClient
      nextPath={resolvedSearchParams?.next}
      previewNotice={isPreviewDeployment ? <PreviewDeploymentNotice /> : null}
    />
  );
}
