import { getBuildInfo, getBuildInfoDiagnostics } from '../build-info';
import SignUpPageClient from './sign-up-page-client';

export const dynamic = 'force-dynamic';

export default function SignUpPage() {
  const buildInfo = getBuildInfo();
  const buildInfoDiagnostics = getBuildInfoDiagnostics(buildInfo);

  return <SignUpPageClient buildInfo={buildInfo} buildInfoDiagnostics={buildInfoDiagnostics} />;
}
