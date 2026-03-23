import { headers } from 'next/headers';

import { getBuildInfo } from '../build-info';
import SignUpPageClient from './sign-up-page-client';

export const dynamic = 'force-dynamic';

export default function SignUpPage() {
  const requestHeaders = headers();
  const currentHost = requestHeaders.get('x-forwarded-host') ?? requestHeaders.get('host');
  const buildInfo = getBuildInfo(process.env, currentHost);

  return <SignUpPageClient buildInfo={buildInfo} />;
}
