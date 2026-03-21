import Link from 'next/link';

import { APP_NAV_ITEMS } from './product-nav';

export default function AppNavigation({ currentPath }: { currentPath: string }) {
  return (
    <nav className="appNav" aria-label="Product navigation">
      {APP_NAV_ITEMS.map((item) => (
        <Link key={item.href} href={item.href} className={currentPath === item.href ? 'active' : ''}>
          {item.label}
        </Link>
      ))}
    </nav>
  );
}
