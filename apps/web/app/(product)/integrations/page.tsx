import IntegrationsPageClient from '../integrations-page-client';

export const dynamic = 'force-dynamic';

// Screen 10 — Integration Gateway. The client reads the canonical control plane via
// the same-origin proxy (/api/integrations/control-plane), so no backend URL is
// threaded through props; the browser never calls the backend directly.
export default function IntegrationsPage() {
  return <IntegrationsPageClient />;
}
