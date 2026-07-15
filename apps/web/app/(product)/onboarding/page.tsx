import { resolveApiUrl } from '../../dashboard-data';
import OnboardingPageClient from '../onboarding-page-client';

export const dynamic = 'force-dynamic';

export default async function OnboardingPage() {
  return <OnboardingPageClient apiUrl={resolveApiUrl()} />;
}
