/**
 * Whether to render the internal (founder) console link, and what to call it.
 *
 * Two facts are kept apart on purpose:
 *
 *   PRIVILEGE   is_internal_admin — a property of the ACCOUNT
 *   PLAN        pilot / scale / enterprise — a property of the ORGANIZATION
 *
 * They never combine. Being internal staff grants no entitlement, raises no
 * limit, and unlocks no execution; the founder working inside a Pilot workspace
 * sees exactly the Pilot limits a customer sees. Nothing here reads a plan.
 *
 * This decides RENDERING only. The link's absence hides a route; it does not
 * protect one, and its presence authorizes nothing — every internal request is
 * authorized server-side against users.is_internal_admin (or the exact-address
 * deployment allowlist) before any customer data is read. A user who edits this
 * flag in their browser gets a link that returns 403.
 */

export const INTERNAL_ADMIN_HREF = '/admin/customers';

export const INTERNAL_ADMIN_LABEL = 'Customer Admin';

/** The single field this decision reads. */
export type InternalAdminSubject = { is_internal_admin?: boolean } | null | undefined;

/**
 * True only when the backend has positively stated internal-admin access.
 *
 * Anything else — no user, a field the API did not send, `null`, or a truthy
 * lookalike such as the string `'false'` — reads as NOT internal staff. The
 * default is the customer view, so a hydration gap can never surface an
 * internal affordance to a customer.
 */
export function showsInternalAdminLink(user: InternalAdminSubject): boolean {
  return user?.is_internal_admin === true;
}
