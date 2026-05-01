import type { ReactNode } from 'react';

export default function ResponseActionPanel({ children }: { children: ReactNode }) {
  return <section aria-label="Response Actions">{children}</section>;
}
