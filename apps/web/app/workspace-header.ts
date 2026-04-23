const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export function normalizeWorkspaceHeaderValue(workspaceId: string | null | undefined): string | null {
  const parts = String(workspaceId || '')
    .split(',')
    .map((value) => value.trim())
    .filter(Boolean);

  for (const candidate of parts) {
    if (UUID_PATTERN.test(candidate)) {
      return candidate;
    }
  }

  return null;
}
