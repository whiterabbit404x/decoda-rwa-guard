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
 *  • Only opacity/transform and paint properties animate — the element keeps
 *    its box, so revealing cannot shift layout.
 *
 * ── Why the previous trigger made the motion invisible ──────────────────────
 *
 * The threshold was a fraction OF THE ELEMENT: 18% of the observed block had
 * to be inside the reading area. That is the right question for a short block
 * and the wrong one for a tall one. The operating layer is one 806px block on
 * a 900px screen, so 18% of it is its top 145px — the eyebrow. Measured in the
 * browser, it fired with its top at y=601 and 63% of itself still below the
 * fold: the four pillars and the lifecycle ribbon were off-screen for the
 * whole sequence, which finished ~1.3s later, long before the reader scrolled
 * to them. Every choreographed block on the page had already played by the
 * time it was looked at. That is what "the page feels static" was.
 *
 * Two things fix it, and both are needed:
 *
 *  1. The trigger is measured in SCREEN pixels, not element percent. A block
 *     reveals once `REVEAL_RATIO` of it is inside the reading area, but never
 *     before at least `MIN_READING_PX` of it is on screen — so a tall section
 *     waits until the reader is actually looking at it rather than at its top
 *     edge. `MAX_READING_SHARE` caps the wait so a very tall block does not
 *     hold out for an impossible amount of screen.
 *  2. Sections that choreograph several blocks (the operating layer) observe
 *     each block separately, so the sequence plays where the reader's eye is.
 *
 * The observer and the load-time check share one predicate — `arrived()` — so
 * a section peeking below the fold on a tall screen can never be written off
 * as "already read" by one rule and staged by the other.
 */

/** Fraction of the block that must be inside the reading area to trigger. */
const REVEAL_RATIO = 0.22;
/** Share of the viewport trimmed off the bottom, so the fold is not the line. */
const READING_INSET = 0.12;
/** …but at least this many pixels of it must be on screen, however tall it is. */
const MIN_READING_PX = 150;
/** …and never demand more than this share of the screen, however tall it is. */
const MAX_READING_SHARE = 0.42;

let observer: IntersectionObserver | null = null;
let observedCount = 0;

function prefersReducedMotion(): boolean {
  return typeof window.matchMedia === 'function'
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

function reveal(el: Element): void {
  el.setAttribute('data-reveal', 'in');
}

function viewportHeight(): number {
  return window.innerHeight || document.documentElement.clientHeight || 0;
}

/**
 * How much of a block of `blockHeight` must be inside the reading area before
 * it counts as arrived. Percent of the block, floored and capped in screen
 * pixels so the answer stays sensible for a 100px heading and for a 900px
 * section alike.
 */
function requiredVisiblePx(blockHeight: number): number {
  const viewport = viewportHeight();
  const wanted = Math.max(blockHeight * REVEAL_RATIO, MIN_READING_PX);
  return Math.min(wanted, blockHeight, viewport * MAX_READING_SHARE);
}

/**
 * The single trigger predicate, in screen coordinates.
 *
 * `top <= 0` catches a fast scroll past a block taller than the reading area,
 * which can never satisfy the pixel rule on its own.
 */
function arrived(rect: { top: number; height: number }, visiblePx: number): boolean {
  if (rect.height <= 0) {
    return false;
  }
  return rect.top <= 0 || visiblePx >= requiredVisiblePx(rect.height);
}

/** Height of the block currently inside the reading area. */
function visibleHeight(rect: { top: number; bottom: number }): number {
  const readingBottom = viewportHeight() * (1 - READING_INSET);
  return Math.min(rect.bottom, readingBottom) - Math.max(rect.top, 0);
}

/** Whether a block has already reached reading position at load time. */
function hasArrived(el: Element): boolean {
  const rect = el.getBoundingClientRect();
  return arrived(rect, visibleHeight(rect));
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
          // intersectionRect is already measured against the inset root, so
          // this is the same number visibleHeight() computes by hand.
          if (!entry.isIntersecting
            || !arrived(entry.boundingClientRect, entry.intersectionRect.height)) {
            continue;
          }
          // Set the attribute directly: no React state, so revealing a section
          // never re-renders the tree around it.
          reveal(entry.target);
          observer?.unobserve(entry.target);
        }
      },
      {
        // The bottom inset pulls the trigger line up out of the fold, so a
        // block starts moving as it reaches reading position — not as its
        // first few pixels appear.
        rootMargin: `0px 0px -${READING_INSET * 100}% 0px`,
        // Coarse steps only: they wake the callback, which then applies the
        // pixel rule above. A threshold cannot express "150px of whatever
        // this block is", so it must not be the thing deciding.
        threshold: [0, 0.1, 0.25, 0.5, 0.75, 1],
      },
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
