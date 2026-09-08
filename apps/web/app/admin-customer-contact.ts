/**
 * How the internal customers console names ONE human per organization.
 *
 * Pure adapters over the GET /admin/customers payload — no fetching, no React —
 * so the rules below are directly unit-testable.
 *
 * The choice itself is the BACKEND's: owner, then admin, then earliest member,
 * resolved in the listing query against real membership rows. Nothing here
 * re-derives a contact, guesses one from an organization name or slug, or falls
 * back to any other address. An organization whose membership resolves to
 * nobody renders the em dash — "we have no contact on record" — because a
 * plausible-looking address the founder might actually email would be worse
 * than an obvious blank.
 *
 * The address is all this surface carries. No credential, session, or
 * authentication field is part of the payload, and none is rendered here.
 */

/** The contact-bearing fields of one row of GET /admin/customers. */
export interface AdminCustomerContact {
  primary_contact_email?: string | null;
  /** Total organization_memberships rows, contact included. */
  members?: number | null;
}

/** No contact on record. Rendered instead of an invented or blank address. */
export const NO_PRIMARY_CONTACT = '—';

/**
 * The address to display, or null when the backend resolved none.
 *
 * A non-string, empty, or whitespace-only value reads as "none" — the same as
 * an absent field — so a partial payload can only ever under-claim.
 */
export function primaryContactEmail(customer: AdminCustomerContact | null | undefined): string | null {
  const value = customer?.primary_contact_email;
  if (typeof value !== 'string') {
    return null;
  }
  const trimmed = value.trim();
  return trimmed === '' ? null : trimmed;
}

/** The cell's text: the address, or the em dash when there is none. */
export function primaryContactLabel(customer: AdminCustomerContact | null | undefined): string {
  return primaryContactEmail(customer) ?? NO_PRIMARY_CONTACT;
}

/**
 * "+2 members" — the members this table is NOT showing — or null.
 *
 * Counted from the membership total the listing already returns, so it costs no
 * extra request. Null (nothing rendered) whenever the count is missing, not a
 * usable number, or leaves no one out; the main table stays one contact wide.
 */
export function otherMembersLabel(customer: AdminCustomerContact | null | undefined): string | null {
  const total = customer?.members;
  if (typeof total !== 'number' || !Number.isFinite(total)) {
    return null;
  }
  // Everyone except the one contact this row already names. With no contact
  // resolved, no member has been named and none can be described as "other".
  const others = primaryContactEmail(customer) === null ? 0 : Math.floor(total) - 1;
  if (others <= 0) {
    return null;
  }
  return `+${others} ${others === 1 ? 'member' : 'members'}`;
}
