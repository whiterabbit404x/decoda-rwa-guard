import { headers } from 'next/headers';

import BuildIdentityBadge, { getBuildVersionLine } from '../build-identity-badge';
import { getBuildInfo } from '../build-info';
import PreviewDeploymentNotice from '../preview-deployment-notice';
import SignUpPageClient from './sign-up-page-client';

export const dynamic = 'force-dynamic';

export default function SignUpPage() {
  const requestHeaders = headers();
  const requestHost = requestHeaders.get('x-forwarded-host') ?? requestHeaders.get('host');
  const buildInfo = getBuildInfo(process.env, { host: requestHost });
  const isPreviewDeployment = process.env.VERCEL_ENV === 'preview';

  return (
    <SignUpPageClient
      buildBadge={<BuildIdentityBadge buildInfo={buildInfo} />}
      versionLine={getBuildVersionLine(buildInfo)}
      previewNotice={isPreviewDeployment ? <PreviewDeploymentNotice /> : null}
    />
  );
}
