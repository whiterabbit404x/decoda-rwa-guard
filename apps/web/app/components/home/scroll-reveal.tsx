'use client';

import { useEffect, useRef, type ElementType, type ReactNode } from 'react';

import styles from './home.module.css';

/**
 * One-time scroll entrance for the landing page's secondary/tertiary motion.
 *
 * Design constraints this satisfies:
 *  • No animation dependency — a single shared IntersectionObserver.
 *  • No inline styles — the production CSP forbids the `style` attribute, so
 *    the hidden/visible states and every stagger delay live in the CSS module.
 *  • Content is readable without JavaScript. The hidden start state is gated
 *    behind `html[data-landing-reveal='on']`, an attribute only this component
 *    sets, so a visitor whose JS never runs simply reads the finished page.
 *  • Reduced motion never opts in at all, so no observer work happens and the
 *    content renders in its final state.
 *  • Only opacity/transform animate — the element keeps its box, so revealing
 *    cannot shift layout.
 */

const REVEAL_FLAG = 'landingReveal';

let observer: IntersectionObserver | null = null;
let observedCount = 0;

function prefersReducedMotion(): boolean {
  return typeof window.matchMedia === 'function'
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

/** Lazily created, shared by every <ScrollReveal> on the page. */
function getObserver(): IntersectionObserver | null {
  if (typeof IntersectionObserver === 'undefined') {
    return null;
  }
  if (!observer) {
    observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) {
            continue;
          }
          // Set the attribute directly: no React state, so revealing a section
          // never re-renders the tree around it.
          entry.target.setAttribute('data-reveal', 'in');
          observer?.unobserve(entry.target);
        }
      },
      { rootMargin: '0px 0px -12% 0px', threshold: 0.06 },
    );
  }
  return observer;
}

export function ScrollReveal({
  children,
  className,
  as: Tag = 'div',
  stagger = false,
}: {
  children: ReactNode;
  className?: string;
  /** Element to render. Defaults to a plain div. */
  as?: ElementType;
  /** Fade the direct children in sequence instead of the wrapper as a whole. */
  stagger?: boolean;
}) {
  const ref = useRef<HTMLElement | null>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el || prefersReducedMotion()) {
      return undefined;
    }

    const io = getObserver();
    if (!io) {
      return undefined;
    }

    // Opt the document in only once we know the reveal can actually run.
    document.documentElement.dataset[REVEAL_FLAG] = 'on';
    io.observe(el);
    observedCount += 1;

    return () => {
      io.unobserve(el);
      observedCount -= 1;
      if (observedCount <= 0) {
        // Leaving the landing page: drop the flag so the attribute never
        // lingers on an authenticated route.
        delete document.documentElement.dataset[REVEAL_FLAG];
        observer?.disconnect();
        observer = null;
        observedCount = 0;
      }
    };
  }, []);

  const revealClass = stagger ? styles.revealStagger : styles.reveal;

  return (
    <Tag ref={ref} className={className ? `${revealClass} ${className}` : revealClass}>
      {children}
    </Tag>
  );
}
