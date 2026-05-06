import Link from 'next/link';

import { APP_NAV_ITEMS } from './product-nav';
import { NAV_ICONS } from './nav-icons';

export default function AppNavigation({ currentPath }: { currentPath: string }) {
  return (
    <nav className="appNav" aria-label="Product navigation">
      {APP_NAV_ITEMS.map((item) => {
        const NavIcon = NAV_ICONS[item.href];
        const isActive = currentPath === item.href || (item.href !== '/dashboard' && currentPath.startsWith(item.href + '/'));

        return (
          <Link
            key={item.href}
            href={item.href}
            prefetch={false}
            className={isActive ? 'active' : ''}
            aria-current={isActive ? 'page' : undefined}
          >
            <span className="appNavIcon">
              {NavIcon ? <NavIcon size={15} /> : <span aria-hidden="true">{item.label[0]}</span>}
            </span>
            <span className="appNavLabel">{item.label}</span>
          </Link>
        );
      })}
    </nav>
  );
}
