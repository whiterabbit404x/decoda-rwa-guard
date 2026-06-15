import { normalizeApiBaseUrl } from '../../../api-config';
import { getRuntimeConfig } from '../../../runtime-config';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

function maskApiUrl(url: string): string {
  try {
    const parsed = new URL(url);
    return `${parsed.protocol}//${parsed.host}/[...]`;
  } catch {
    return '[masked]';
  }
}

export async function GET() {
  const runtimeConfig = getRuntimeConfig();
  const backendApiUrl = normalizeApiBaseUrl(runtimeConfig.apiUrl);

  if (!runtimeConfig.configured || !backendApiUrl) {
    return Response.json(
      {
        reachable: false,
        configured: false,
        reason: 'api_url_not_configured',
        diagnostic: runtimeConfig.diagnostic,
      },
      { headers: { 'Cache-Control': 'no-store' } },
    );
  }

  const maskedUrl = maskApiUrl(backendApiUrl);

  try {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 5000);
    let response: Response;

    try {
      response = await fetch(`${backendApiUrl}/health`, {
        cache: 'no-store',
        signal: controller.signal,
      });
    } finally {
      clearTimeout(timeoutId);
    }

    const body: Record<string, unknown> = response.ok
      ? await response.json().catch(() => ({}))
      : {};

    return Response.json(
      {
        reachable: response.ok,
        status: response.status,
        configured: true,
        api_url: maskedUrl,
        backend_service: typeof body.service === 'string' ? body.service : null,
        backend_git_commit: typeof body.backend_git_commit === 'string' ? body.backend_git_commit : null,
        backend_build_id: typeof body.backend_build_id === 'string' ? body.backend_build_id : null,
      },
      { headers: { 'Cache-Control': 'no-store' } },
    );
  } catch (err) {
    const errorType = err instanceof Error ? err.name : 'UnknownError';
    const errorMessage = err instanceof Error ? err.message : String(err);

    console.error(
      JSON.stringify({
        event: 'auth_backend_health_check_failed',
        api_url: maskedUrl,
        error_type: errorType,
        error_message: errorMessage,
      }),
    );

    return Response.json(
      {
        reachable: false,
        configured: true,
        status: null,
        api_url: maskedUrl,
        error_type: errorType,
        reason: 'network_error',
      },
      { headers: { 'Cache-Control': 'no-store' } },
    );
  }
}
