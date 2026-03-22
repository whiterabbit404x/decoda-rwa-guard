import PreviewDeploymentNotice from '../preview-deployment-notice';
import SignInPageClient from './sign-in-page-client';

export const dynamic = 'force-dynamic';

export default function SignInPage({ searchParams }: { searchParams?: { next?: string } }) {
  const isPreviewDeployment = process.env.VERCEL_ENV === 'preview';

  return <SignInPageClient nextPath={searchParams?.next} previewNotice={isPreviewDeployment ? <PreviewDeploymentNotice /> : null} />;
}
