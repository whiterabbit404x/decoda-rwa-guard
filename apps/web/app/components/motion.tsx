'use client';

import { useEffect, useRef, useState } from 'react';

/**
 * Motion primitives.
 *
 * Almost all product motion is CSS (see the "Motion" tokens and the brand
 * layer in styles.css), because CSS animation needs no re-render, survives
 * polling without remounting anything, and is collapsed in one place by
 * `prefers-reduced-motion`. The only thing CSS cannot do is interpolate a
 * NUMBER, so that is the one primitive here.
 */

const REDUCED_MOTION_QUERY = '(prefers-reduced-motion: reduce)';

export function prefersReducedMotion(): boolean {
  if (typeof window === 'undefined' || !window.matchMedia) return true;
  return window.matchMedia(REDUCED_MOTION_QUERY).matches;
}

/**
 * Tween a displayed number from its previous REAL value to its new REAL
 * value when the backend reports a change.
 *
 * Truthfulness rules this follows:
 *   • The first render shows the real value immediately. Nothing counts up
 *     from zero, so no value the backend never reported is ever shown on load.
 *   • A change tweens only between two backend-reported values, briefly
 *     (≤ 450ms), and lands exactly on the new value.
 *   • Reduced motion, a non-finite value, or a hidden tab: the new value is
 *     shown at once.
 */
export function useTweenedNumber(value: number, durationMs = 450): number {
  const [display, setDisplay] = useState(value);
  const previous = useRef(value);

  useEffect(() => {
    const from = previous.current;
    previous.current = value;
    if (from === value || !Number.isFinite(from) || !Number.isFinite(value)) {
      setDisplay(value);
      return;
    }
    if (prefersReducedMotion() || (typeof document !== 'undefined' && document.hidden)) {
      setDisplay(value);
      return;
    }

    let frame = 0;
    const start = performance.now();
    const step = (now: number) => {
      const progress = Math.min(1, (now - start) / durationMs);
      const eased = 1 - Math.pow(1 - progress, 3);
      setDisplay(progress >= 1 ? value : from + (value - from) * eased);
      if (progress < 1) frame = requestAnimationFrame(step);
    };
    frame = requestAnimationFrame(step);
    return () => {
      cancelAnimationFrame(frame);
      setDisplay(value);
    };
  }, [value, durationMs]);

  return display;
}

/**
 * A metric number that eases between real values on change.
 *
 * `format` receives the in-flight value; integers are rounded so a count
 * never shows a fraction.
 */
export function AnimatedNumber({
  value,
  format,
  className,
}: {
  value: number;
  format?: (n: number) => string;
  className?: string;
}) {
  const shown = useTweenedNumber(value);
  const isInteger = Number.isInteger(value);
  const render = format ?? ((n: number) => String(n));
  const settled = render(value);
  const text = shown === value ? settled : render(isInteger ? Math.round(shown) : shown);

  // One text node: the DOM always settles on exactly the backend value, so
  // anything reading the card (a test, a screen reader, copy-paste) gets the
  // real figure, never a duplicate.
  return (
    <span className={`dcAnimatedNumber${className ? ` ${className}` : ''}`} data-value={settled}>
      {text}
    </span>
  );
}
