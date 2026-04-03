import { cookies } from 'next/headers';
import { redirect } from 'next/navigation';

import PreviewDeploymentNotice from '../preview-deployment-notice';
import { getRuntimeConfig } from '../runtime-config';
import SignUpPageClient from './sign-up-page-client';

export const dynamic = 'force-dynamic';

export default async function SignUpPage() {
  const isPreviewDeployment = process.env.VERCEL_ENV === 'preview';
  const runtimeConfig = getRuntimeConfig();
  const cookieStore = await cookies();
  const token = cookieStore.get('decoda_session')?.value;

  if (runtimeConfig.liveModeEnabled && token) {
    redirect('/dashboard');
  }

  return <SignUpPageClient previewNotice={isPreviewDeployment ? <PreviewDeploymentNotice /> : null} />;
}
