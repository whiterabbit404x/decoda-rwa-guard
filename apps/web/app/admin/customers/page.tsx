import AdminCustomersClient from './admin-customers-client';

export const dynamic = 'force-dynamic';

export const metadata = {
  title: 'Customers · Decoda internal',
  // Not a customer surface: keep it out of search indexes as well as out of the
  // product navigation.
  robots: { index: false, follow: false },
};

/**
 * Internal founder console.
 *
 * This route sits OUTSIDE the (product) route group on purpose, so it never
 * appears in the customer app shell or its navigation. That is a presentation
 * choice, not a control: authorization happens on every backend request against
 * `users.is_internal_admin` (or the exact-address deployment allowlist), and a
 * customer who navigates here directly receives 403 and sees no organization
 * data at all.
 */
export default function AdminCustomersPage() {
  return <AdminCustomersClient />;
}
