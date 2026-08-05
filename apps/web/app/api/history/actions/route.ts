import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Same-origin proxy for GET /history/actions (the Response Actions "Action History" tab, which
// renders the immutable audit stream — response-action approvals, rejections, blocked attempts,
// simulations, executions, rollbacks — newest first). The browser must NOT call the backend
// directly: only NEXT_PUBLIC_API_URL is exposed client-side and it is unset in this deployment,
// so a direct `${apiUrl}/history/actions` fetch resolves to the same-origin Next server (which
// serves no such route), fails, and the client silently renders an EMPTY history. That is the
// exact reason a successful response-action approval — persisted as a `response_action.approved`
// action_history event — never appeared in Action History, leaving only the recommendation-review
// row (which comes from the already-proxied /api/response/actions read). Routing through this
// proxy is the same transport the Alerts/Incidents lists and runtime-status already use. Query
// params (object_type, object_id, limit) are forwarded verbatim.
export async function GET(request: Request): Promise<Response> {
  const url = new URL(request.url);
  return proxyJsonToBackend(request, {
    backendPath: '/history/actions',
    method: 'GET',
    searchParams: url.searchParams,
  });
}
