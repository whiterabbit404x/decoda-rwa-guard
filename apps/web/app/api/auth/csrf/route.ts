import { dynamic, getCsrfToken, revalidate } from 'app/api/auth/_shared/proxy';

export { dynamic, revalidate };

export async function GET() {
  return Response.json({ csrfToken: await getCsrfToken() }, { headers: { 'Cache-Control': 'no-store' } });
}
