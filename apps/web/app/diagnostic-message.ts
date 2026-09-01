// Does this message name deployment configuration rather than describe something
// the customer can act on?
//
// Runtime-config diagnostics ("API URL source: NEXT_PUBLIC_API_URL.") are
// operator telemetry. They are useful on the sign-in diagnostics panel, which is
// opt-in, and they are noise — or worse, alarming red text naming internal
// environment variables — anywhere in the product UI.
//
// Lives in its own module because two surfaces need the same predicate and a
// third will eventually: a filter that exists in only one component is a filter
// the next component forgets.
// `API URL source` is matched as well as the env-var names because
// runtime-config.ts composes every diagnostic behind describeApiUrlSource(), and
// two of those branches ("explicit local fallback", "invalid") name no
// environment variable at all — an env-var-only filter lets them through.
export function containsDiagnosticEnvVars(message: string): boolean {
  return /API_URL|NEXT_PUBLIC|ALLOW_LOCAL_API_FALLBACK|LIVE_MODE_ENABLED|API URL source/i.test(message);
}
