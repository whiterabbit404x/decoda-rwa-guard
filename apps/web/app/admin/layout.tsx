import AdminConsoleNav from './admin-console-nav';

/**
 * Founder console frame for every /admin route.
 *
 * Outside the (product) route group on purpose: the console never appears in
 * the customer app shell. The navigation renders only after the backend
 * confirms internal-admin access; each page still authorizes every request on
 * the server and answers a customer with 403.
 */
export default function AdminLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="adminShell">
      <AdminConsoleNav />
      {children}
    </div>
  );
}
