// Validate a ?return_to= value as a SAFE, SAME-ORIGIN internal path so a crafted value can
// never turn a post-MFA redirect (Screen 8 -> Security Settings -> back to the response
// action) into an open redirect off the app.
//
// Accepted: a single root-relative path beginning with one '/', e.g. '/response-actions?...'.
// Rejected:
//   - protocol-relative targets ('//host' and the '/\host' browsers normalize to it),
//   - scheme URLs (javascript:, data:, http(s): — none begin with '/'),
//   - control characters / whitespace that could smuggle a second URL,
//   - absurdly long values.
// Path traversal ('/../x') stays same-origin — the browser resolves it within this origin —
// so it is not an open-redirect risk and is intentionally allowed through.
export function safeInternalReturnTo(raw: string | null | undefined): string | null {
  if (!raw) {
    return null;
  }
  if (raw.length > 2048) {
    return null;
  }
  // Reject whitespace (a raw newline/tab could smuggle a second URL) ...
  if (/\s/.test(raw)) {
    return null;
  }
  // ... and any other control character (0x00-0x1f and DEL 0x7f).
  for (let i = 0; i < raw.length; i += 1) {
    const code = raw.charCodeAt(i);
    if (code < 0x20 || code === 0x7f) {
      return null;
    }
  }
  if (!raw.startsWith('/')) {
    return null;
  }
  if (raw[1] === '/' || raw[1] === '\\') {
    return null;
  }
  return raw;
}
