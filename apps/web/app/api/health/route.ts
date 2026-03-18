export async function GET(): Promise<Response> {
  return Response.json({ ok: true, service: 'web', timestamp: new Date().toISOString() });
}
