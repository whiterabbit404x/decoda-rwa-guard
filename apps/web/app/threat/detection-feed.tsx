import type { ReactNode } from 'react';

export default function DetectionFeed({ children }: { children: ReactNode }) {
  return <section aria-label="Detection Feed">{children}</section>;
}
