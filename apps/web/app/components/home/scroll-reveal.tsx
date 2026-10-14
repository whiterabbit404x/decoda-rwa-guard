'use client';

import { createElement, useEffect, useRef, type ReactNode } from 'react';

import styles from './site.module.css';

/**
 * One-time scroll entrances for the public website.
 *
 *   <Reveal>       the block rises (or fades, scales, slides) in as one piece
 *   <RevealGroup>  the block stays put and its direct children enter in turn
 *
 * Design constraints this satisfies:
 *  • No animation dependency — one shared IntersectionObserver for the page.
 *  • No inline styles — the production CSP forbids the `style` attribute, so
 *    the hidden state, the entrance, every variant, stagger and delay live in
 *    site.module.css and are selected by attributes/classes.
 *  • Readable without JavaScript. The hidden state is keyed on `data-reveal`,
 *    an attribute only this component sets, so a visitor whose JS never runs
 *    reads the finished page.
 *  • Reduced motion never opts in: no attribute, no observer work, nothing
 *    hidden — the content renders in its final state.
 *  • A block already in the reading area when the page loads is marked
 *    `done`, never animated: it was painted visible, and hiding it to fade it
 *    back in would be a flicker, not an entrance.
 *  • Only opacity and transform animate, so an entrance cannot shift layout.
 *  • Revealing sets an attribute on the DOM node — no React state, so it never
 *    re-renders the tree around it.
 *
 * ── When a block counts as "arrived" ────────────────────────────────────────
 *
 * A bare percent-of-the-element threshold is the right question for a short
 * block and the wrong one for a tall one: 20% of an 800px section is its top
 * sliver, so the whole sequence would play while most of it was still below
 * the fold. So the trigger is measured in SCREEN pixels: a block reveals once
 * `REVEAL_RATIO` of it is inside the reading area, but never before at least
 * `MIN_READING_PX` of it is on screen, and never demanding more than
 * `MAX_READING_SHARE` of the screen. The observer and the load-time check
 * share one predicate — `arrived()` — so a block cannot be staged by one rule
 * and written off as already read by the other.
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

/** Lazily created, shared by every reveal on the page. */
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
          entry.target.setAttribute('data-reveal', 'in');
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

/** Stage the element below the reading line; reveal it once, when it arrives. */
function useReveal<T extends HTMLElement>() {
  const ref = useRef<T | null>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el || prefersReducedMotion()) {
      return undefined;
    }

    const io = getObserver();
    if (!io) {
      return undefined;
    }

    // Already in the reading area at load (a tall screen, a deep link, a
    // restored scroll position): it was painted visible, so it stays visible.
    if (hasArrived(el)) {
      el.setAttribute('data-reveal', 'done');
      return undefined;
    }

    el.setAttribute('data-reveal', 'out');
    io.observe(el);
    observedCount += 1;

    return () => {
      io.unobserve(el);
      observedCount -= 1;
      if (observedCount <= 0) {
        // Leaving the page: drop the shared observer entirely.
        observer?.disconnect();
        observer = null;
        observedCount = 0;
      }
    };
  }, []);

  return ref;
}

type Tag = 'div' | 'section' | 'ul' | 'ol' | 'li' | 'article' | 'aside' | 'figure' | 'header' | 'dl' | 'p';

export type RevealVariant = 'up' | 'fade' | 'scale' | 'left' | 'right';

type RevealProps = {
  children?: ReactNode;
  className?: string;
  /** Element to render. Defaults to a plain div. */
  as?: Tag;
  /** Entrance style. Default: rise and fade in. */
  variant?: RevealVariant;
  id?: string;
  role?: string;
  'aria-label'?: string;
  'aria-labelledby'?: string;
};

function classes(base: string, extra?: string): string {
  return extra ? `${base} ${extra}` : base;
}

/** Reveals a block once, the first time it reaches the reading area. */
export function Reveal({ children, className, as = 'div', variant = 'up', ...rest }: RevealProps) {
  const ref = useReveal<HTMLElement>();
  return createElement(
    as,
    {
      ...rest,
      ref,
      className: classes(styles.reveal, className),
      'data-variant': variant === 'up' ? undefined : variant,
    },
    children,
  );
}

/**
 * The same one-shot reveal for a list or grid: the wrapper stays put and its
 * direct children enter one after another. Pass the grid class through
 * `className` so this element IS the grid — no extra wrapper.
 */
export function RevealGroup({ children, className, as = 'div', variant = 'up', ...rest }: RevealProps) {
  const ref = useReveal<HTMLElement>();
  return createElement(
    as,
    {
      ...rest,
      ref,
      className: classes(styles.revealGroup, className),
      'data-variant': variant === 'up' ? undefined : variant,
    },
    children,
  );
}
