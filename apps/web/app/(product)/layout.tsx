import { Suspense } from 'react';

import { cookies } from 'next/headers';
import { redirect } from 'next/navigation';

import AppShell from '../app-shell';
import AuthenticatedRoute from '../authenticated-route';
import { getRuntimeConfig } from '../runtime-config';
import { shouldRedirectUnauthenticatedProductAccess } from '../auth-guards';

const TOKEN_COOKIE_NAME = 'decoda-pilot-access-token';

function ProductLayoutLoading({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}

export default function ProductLayout({ children }: { children: React.ReactNode }) {
  const token = cookies().get(TOKEN_COOKIE_NAME)?.value;
  const runtimeConfig = getRuntimeConfig();

  if (shouldRedirectUnauthenticatedProductAccess(token, runtimeConfig)) {
    redirect('/sign-in');
  }

  return (
    <AppShell>
      <Suspense fallback={<ProductLayoutLoading>{children}</ProductLayoutLoading>}>
        <AuthenticatedRoute>{children}</AuthenticatedRoute>
      </Suspense>
    </AppShell>
  );
}
