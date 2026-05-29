import { getCsrfToken } from 'app/api/auth/_shared/proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

export async function GET() {
  return Response.json({ csrfToken: await getCsrfToken() }, { headers: { 'Cache-Control': 'no-store' } });
}
