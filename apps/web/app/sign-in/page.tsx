import { headers } from 'next/headers';

import BuildIdentityBadge, { getBuildVersionLine } from '../build-identity-badge';
import { getBuildInfo } from '../build-info';
import PreviewDeploymentNotice from '../preview-deployment-notice';
import SignInPageClient from './sign-in-page-client';

export const dynamic = 'force-dynamic';

export default function SignInPage({ searchParams }: { searchParams?: { next?: string } }) {
  const requestHeaders = headers();
  const requestHost = requestHeaders.get('x-forwarded-host') ?? requestHeaders.get('host');
  const buildInfo = getBuildInfo(process.env, { host: requestHost });
  const isPreviewDeployment = process.env.VERCEL_ENV === 'preview';

  return (
    <SignInPageClient
      nextPath={searchParams?.next}
      buildBadge={<BuildIdentityBadge buildInfo={buildInfo} />}
      versionLine={getBuildVersionLine(buildInfo)}
      previewNotice={isPreviewDeployment ? <PreviewDeploymentNotice /> : null}
    />
  );
}
