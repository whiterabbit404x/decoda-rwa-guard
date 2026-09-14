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
 *  • Content is readable without JavaScript. Both the hidden and the revealed
 *    rules are keyed on `data-reveal`, an attribute only this component sets,
 *    so a visitor whose JS never runs simply reads the finished page.
 *  • Reduced motion never opts in at all, so no observer work happens and the
 *    content renders in its final state.
 *  • Only opacity/transform animate — the element keeps its box, so revealing
 *    cannot shift layout.
 *
 * Two details are what make the motion actually visible, and both were wrong
 * before:
 *
 *  1. The hidden state is per-element (`data-reveal="out"`), not a document
 *     flag. A document flag set in an effect hides every reveal target *after*
 *     first paint, and the observer then promotes the on-screen ones back in
 *     the very next task — the two style changes coalesce into one recalc and
 *     nothing animates at all (and anything already readable briefly fades
 *     out first). An element that is already on screen when the page loads is
 *     therefore sent straight to "in" here, never through "out".
 *  2. The trigger is a real reading position. A bare 6%-of-the-element
 *     threshold is a few dozen pixels on a tall grid, so a section would
 *     "enter" while still below the fold and finish animating long before the
 *     visitor arrived.
 */

/** Fraction of the element that must be inside the reading area to trigger. */
const REVEAL_RATIO = 0.18;
/** Share of the viewport trimmed off the bottom, so the fold is not the line. */
const READING_INSET = 0.15;

let observer: IntersectionObserver | null = null;
let observedCount = 0;

function prefersReducedMotion(): boolean {
  return typeof window.matchMedia === 'function'
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

function reveal(el: Element): void {
  el.setAttribute('data-reveal', 'in');
}

/**
 * Whether an element has already reached reading position, measured the same
 * way the observer measures it: enough of the element inside a viewport
 * shortened at the bottom, or its top already past the top of the screen.
 */
function hasArrived(el: Element): boolean {
  const rect = el.getBoundingClientRect();
  if (rect.height <= 0) {
    return false;
  }
  if (rect.top <= 0 && rect.bottom > 0) {
    return true;
  }
  const viewport = window.innerHeight || document.documentElement.clientHeight;
  const readingBottom = viewport * (1 - READING_INSET);
  const visible = Math.min(rect.bottom, readingBottom) - Math.max(rect.top, 0);
  return visible / rect.height >= REVEAL_RATIO;
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
          // `top <= 0` catches a fast scroll past an element taller than the
          // reading area, which can never reach the ratio on its own.
          const arrived = entry.isIntersecting
            && (entry.intersectionRatio >= REVEAL_RATIO || entry.boundingClientRect.top <= 0);
          if (!arrived) {
            continue;
          }
          // Set the attribute directly: no React state, so revealing a section
          // never re-renders the tree around it.
          reveal(entry.target);
          observer?.unobserve(entry.target);
        }
      },
      // The bottom inset pulls the trigger line up out of the fold, so a
      // section starts moving as it reaches reading position — not as its
      // first few pixels appear.
      { rootMargin: `0px 0px -${READING_INSET * 100}% 0px`, threshold: [0, REVEAL_RATIO] },
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

    // Already arrived at load (a short viewport, a deep link, a restored
    // scroll position): finish it immediately. Going straight to "in" without
    // ever being "out" changes no animatable property, so nothing flickers.
    //
    // This asks the same question the observer asks, against the same reading
    // area — so a section merely peeking below the fold on a tall screen is
    // still staged out and animates when the visitor actually reaches it.
    if (hasArrived(el)) {
      reveal(el);
      return undefined;
    }

    el.setAttribute('data-reveal', 'out');
    io.observe(el);
    observedCount += 1;

    return () => {
      io.unobserve(el);
      observedCount -= 1;
      if (observedCount <= 0) {
        // Leaving the landing page: drop the shared observer entirely.
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
