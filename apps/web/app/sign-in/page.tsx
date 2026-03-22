import { getBuildInfo, getBuildInfoDiagnostics } from '../build-info';
import SignInPageClient from './sign-in-page-client';

export const dynamic = 'force-dynamic';

export default function SignInPage({ searchParams }: { searchParams?: { next?: string } }) {
  const buildInfo = getBuildInfo();
  const buildInfoDiagnostics = getBuildInfoDiagnostics(buildInfo);

  return <SignInPageClient buildInfo={buildInfo} buildInfoDiagnostics={buildInfoDiagnostics} nextPath={searchParams?.next} />;
}
