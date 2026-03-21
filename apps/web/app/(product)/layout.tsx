import { cookies } from 'next/headers';
import { redirect } from 'next/navigation';

import AppShell from '../app-shell';
import AuthenticatedRoute from '../authenticated-route';

const TOKEN_COOKIE_NAME = 'decoda-pilot-access-token';

function isLiveModeEnabledOnWeb() {
  return (process.env.NEXT_PUBLIC_LIVE_MODE_ENABLED ?? '').toLowerCase() === 'true';
}

export default function ProductLayout({ children }: { children: React.ReactNode }) {
  const token = cookies().get(TOKEN_COOKIE_NAME)?.value;

  if (isLiveModeEnabledOnWeb() && !token) {
    redirect('/sign-in');
  }

  return (
    <AppShell>
      <AuthenticatedRoute>{children}</AuthenticatedRoute>
    </AppShell>
  );
}
