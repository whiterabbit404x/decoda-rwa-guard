import OnboardingPageClient from '../onboarding-page-client';

export const dynamic = 'force-dynamic';

// The onboarding client talks to the backend only through same-origin /api/onboarding/*
// proxy routes, so the server-resolved backend URL is intentionally NOT passed to the
// browser (it is often an internal / non-browser-reachable Railway origin).
export default function OnboardingPage() {
  return <OnboardingPageClient />;
}
