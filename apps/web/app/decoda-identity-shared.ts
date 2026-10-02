/**
 * Shared Decoda identity — browser-safe helpers (no secrets, no server APIs).
 *
 * Nothing here decides access: the RWA Guard API checks the Decoda platform's
 * membership and entitlement on every request. These helpers only turn its
 * answers into honest screens.
 */

export type AccessReason =
  | 'not_entitled'
  | 'entitlement_suspended'
  | 'entitlement_expired'
  | 'entitlement_not_started'
  | 'product_unavailable'
  | 'no_membership'
  | 'membership_inactive'
  | 'organization_inactive'
  | 'user_inactive'
  | 'organization_required'
  | 'organization_not_ready'
  | 'organization_link_required'
  | 'organization_link_conflict'
  | 'link_conflict'
  | 'disabled'
  | 'no_workspace'
  | 'impersonation'
  | 'unavailable';

export const ACCESS_REASONS: Record<AccessReason, { title: string; body: string; requestAccess: boolean }> = {
  not_entitled: {
    title: 'Decoda RWA Guard is not enabled for your organization',
    body: 'Your Decoda account is active, but your organization does not have access to RWA Guard. An administrator can request it.',
    requestAccess: true,
  },
  entitlement_suspended: {
    title: "Your organization's RWA Guard access is suspended",
    body: 'Contact your Decoda representative to restore access.',
    requestAccess: false,
  },
  entitlement_expired: {
    title: "Your organization's RWA Guard access has expired",
    body: 'Contact your Decoda representative to renew access.',
    requestAccess: true,
  },
  entitlement_not_started: {
    title: "Your organization's RWA Guard access has not started yet",
    body: 'Access opens on the start date agreed with Decoda.',
    requestAccess: false,
  },
  product_unavailable: {
    title: 'Decoda RWA Guard is not available yet',
    body: 'RWA Guard is not open to organizations at the moment.',
    requestAccess: false,
  },
  no_membership: {
    title: 'You are not a member of this organization',
    body: 'Sign in with the account your organization invited, or choose another organization in the Decoda launcher.',
    requestAccess: false,
  },
  membership_inactive: {
    title: 'Your membership in this organization is not active',
    body: 'Ask an administrator of your organization to restore your membership.',
    requestAccess: false,
  },
  organization_inactive: {
    title: 'This organization is not active on Decoda',
    body: 'Contact your Decoda representative.',
    requestAccess: false,
  },
  user_inactive: {
    title: 'Your Decoda account is not active',
    body: 'Contact your organization administrator.',
    requestAccess: false,
  },
  organization_required: {
    title: 'Choose an organization',
    body: 'Your Decoda account is not associated with an organization yet. Open the Decoda launcher, or accept the invitation your organization sent you.',
    requestAccess: false,
  },
  organization_not_ready: {
    title: 'Your organization is not set up in RWA Guard yet',
    body: "Your organization's Decoda administrator must open RWA Guard first. Try again once they have.",
    requestAccess: false,
  },
  organization_link_required: {
    title: 'Your organization is being moved to Decoda sign-in',
    body: 'Decoda support is finishing the migration of your organization. Try again once they confirm it is complete.',
    requestAccess: false,
  },
  organization_link_conflict: {
    title: 'This organization needs attention from Decoda support',
    body: 'Its RWA Guard workspace is linked inconsistently. Nothing was changed; contact Decoda support.',
    requestAccess: false,
  },
  link_conflict: {
    title: 'Your existing RWA Guard account needs to be linked',
    body: 'An existing RWA Guard account uses your email address. Decoda support links it to your Decoda account — nothing is merged automatically.',
    requestAccess: false,
  },
  disabled: {
    title: 'Your RWA Guard access is disabled',
    body: 'An organization administrator disabled your RWA Guard account.',
    requestAccess: false,
  },
  no_workspace: {
    title: 'You are not a member of any RWA Guard workspace yet',
    body: 'You belong to this organization, but none of its workspaces. Ask an owner to add you.',
    requestAccess: false,
  },
  impersonation: {
    title: 'Impersonated sessions cannot open RWA Guard',
    body: 'Sign in with your own Decoda account.',
    requestAccess: false,
  },
  unavailable: {
    title: 'Decoda sign-in is temporarily unavailable',
    body: 'Nothing was granted. Please try again in a few minutes.',
    requestAccess: false,
  },
};

const CODE_REASONS: Record<string, AccessReason> = {
  ORGANIZATION_REQUIRED: 'organization_required',
  ORGANIZATION_NOT_READY: 'organization_not_ready',
  ORGANIZATION_LINK_REQUIRED: 'organization_link_required',
  ORGANIZATION_LINK_CONFLICT: 'organization_link_conflict',
  IDENTITY_LINK_CONFLICT: 'link_conflict',
  USER_DISABLED: 'disabled',
  NO_WORKSPACE_ACCESS: 'no_workspace',
  IMPERSONATION_NOT_ALLOWED: 'impersonation',
  IDENTITY_PROVIDER_UNAVAILABLE: 'unavailable',
  IDENTITY_DIRECTORY_UNAVAILABLE: 'unavailable',
  IDENTITY_NOT_ENABLED: 'unavailable',
  BFF_REQUIRED: 'unavailable',
};

/**
 * What /sign-in offers under the shared Decoda identity, decided on the server
 * (decodaSignInOptions in app/decoda-identity.ts). Absent in `legacy` mode,
 * where the screen is RWA Guard's own sign-in.
 */
export type DecodaSignInOptions = {
  /** "Sign in with Decoda" (WorkOS AuthKit) is offered. */
  enabled: boolean;
  /** The legacy RWA Guard password form is still offered (dual mode, before the sunset). */
  passwordFormAllowed: boolean;
  /** Where people without access request it: the Decoda website, never RWA Guard. */
  requestAccessUrl: string;
  /** A message for why the visitor is here (signed out, session ended, …). */
  notice: string | null;
};

export function isAccessReason(value: unknown): value is AccessReason {
  return typeof value === 'string' && Object.prototype.hasOwnProperty.call(ACCESS_REASONS, value);
}

/** Map an API refusal (code + optional platform reason) to a screen. */
export function accessReasonFor(code: string, reason?: string | null): AccessReason {
  if (code === 'PRODUCT_ACCESS_DENIED') return isAccessReason(reason) ? reason : 'not_entitled';
  return CODE_REASONS[code] ?? 'unavailable';
}

/** A same-site path to continue to after sign-in. Anything else becomes /dashboard. */
export function safeNextPath(raw: string | null | undefined): string {
  const value = (raw ?? '').trim();
  if (!value.startsWith('/') || value.startsWith('//') || value.startsWith('/\\') || value.includes('://')) return '/dashboard';
  if (/[\u0000-\u001f\u007f]/.test(value) || value.startsWith('/api/') || value.startsWith('/auth/')) return '/dashboard';
  return value.slice(0, 512);
}

/** Codes the API answers when a Decoda session must re-authenticate with Decoda (step-up). */
export const DECODA_REAUTH_CODES = new Set(['DECODA_REAUTHENTICATION_REQUIRED']);

/** Where the browser goes to verify again with Decoda, then return here. */
export function decodaReauthenticationPath(nextPath: string): string {
  return `/auth/sign-in?reauth=1&next=${encodeURIComponent(safeNextPath(nextPath))}`;
}

/**
 * Where a refused Decoda session belongs: the platform withdrew access (403
 * PRODUCT_ACCESS_DENIED) or cannot answer (503). Null for anything else —
 * including a legacy session, which never carries these codes.
 */
export function decodaRefusalPath(status: number, body: unknown): string | null {
  const detail = body && typeof body === 'object' ? (body as { detail?: unknown }).detail : null;
  if (!detail || typeof detail !== 'object') return null;
  const code = typeof (detail as { code?: unknown }).code === 'string' ? (detail as { code: string }).code : '';
  const reason = typeof (detail as { reason?: unknown }).reason === 'string' ? (detail as { reason: string }).reason : null;
  if (status === 403 && code === 'PRODUCT_ACCESS_DENIED') return `/access?reason=${accessReasonFor(code, reason)}`;
  if (status === 503 && code === 'IDENTITY_DIRECTORY_UNAVAILABLE') return '/access?reason=unavailable';
  return null;
}

/**
 * Browser only. For an API answer a page is about to treat as "session
 * missing": when it is in fact the Decoda platform refusing this organization
 * (access withdrawn, directory unavailable), go to /access instead and return
 * true. The RWA Guard session is kept, so access that returns works on the
 * next request; signing out would end the Decoda sign-in for nothing.
 */
export async function followDecodaRefusal(response: Response): Promise<boolean> {
  if (response.status !== 403 && response.status !== 503) return false;
  const body: unknown = await response.clone().json().catch(() => null);
  const path = decodaRefusalPath(response.status, body);
  if (!path) return false;
  window.location.assign(path);
  return true;
}
