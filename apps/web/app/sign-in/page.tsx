import { cookies } from 'next/headers';
import { redirect } from 'next/navigation';

import PreviewDeploymentNotice from '../preview-deployment-notice';
import { getRuntimeConfig } from '../runtime-config';
import SignInPageClient from './sign-in-page-client';

export const dynamic = 'force-dynamic';

export default function SignInPage({ searchParams }: { searchParams?: { next?: string } }) {
  const isPreviewDeployment = process.env.VERCEL_ENV === 'preview';
  const runtimeConfig = getRuntimeConfig();
  const token = cookies().get('decoda-pilot-access-token')?.value;

  if (runtimeConfig.liveModeEnabled && token) {
    redirect('/dashboard');
  }

  return <SignInPageClient nextPath={searchParams?.next} previewNotice={isPreviewDeployment ? <PreviewDeploymentNotice /> : null} />;
}
