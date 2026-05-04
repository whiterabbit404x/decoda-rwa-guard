import Link from 'next/link';

import { APP_NAV_ITEMS } from './product-nav';

export default function AppNavigation({ currentPath }: { currentPath: string }) {
  const navIcon = (label: string) => label.slice(0, 1).toUpperCase();

  return (
    <nav className="appNav" aria-label="Product navigation">
      {APP_NAV_ITEMS.map((item) => (
        <Link
          key={item.href}
          href={item.href}
          prefetch={false}
          // Prevent protected-route prefetch fan-out from hitting multiple backend dashboard endpoints after sign-in.
          className={currentPath === item.href ? 'active' : ''}
        >
          <span className="appNavIcon" aria-hidden="true">{navIcon(item.label)}</span>
          <span>{item.label}</span>
        </Link>
      ))}
    </nav>
  );
}
