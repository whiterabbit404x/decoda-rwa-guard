import PreviewDeploymentNotice from '../preview-deployment-notice';
import SignUpPageClient from './sign-up-page-client';

export const dynamic = 'force-dynamic';

export default function SignUpPage() {
  const isPreviewDeployment = process.env.VERCEL_ENV === 'preview';

  return <SignUpPageClient previewNotice={isPreviewDeployment ? <PreviewDeploymentNotice /> : null} />;
}
