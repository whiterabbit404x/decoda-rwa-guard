import { fetchDashboardPageData } from '../../dashboard-data';
import OnboardingPageClient from '../onboarding-page-client';

export const dynamic = 'force-dynamic';

export default async function OnboardingPage() {
  const data = await fetchDashboardPageData();
  return <OnboardingPageClient apiUrl={data.apiUrl} />;
}
