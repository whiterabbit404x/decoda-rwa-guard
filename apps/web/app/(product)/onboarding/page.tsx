import { resolveApiUrl } from '../../dashboard-data';
import OnboardingPageClient from '../onboarding-page-client';
import RuntimeSummaryPanel from '../../runtime-summary-panel';

export const dynamic = 'force-dynamic';

export default async function OnboardingPage() {
  return <OnboardingPageClient apiUrl={resolveApiUrl()} />;
}
