'use client';

import { useEffect, useRef, useState } from 'react';

/**
 * Motion primitives.
 *
 * Almost all product motion is CSS (see the "Motion" tokens and the brand
 * layer in styles.css), because CSS animation needs no re-render, survives
 * polling without remounting anything, and is collapsed in one place by
 * `prefers-reduced-motion`.
 */

/**
 * A metric number that marks a change without ever showing a value the
 * backend did not report.
 *
 * Truthfulness rules this follows:
 *   • The text is ALWAYS the backend value. There is no tween: interpolating
 *     2 → 10 would put 3…9 on screen, in the accessibility tree and in copied
 *     text — counts the workspace never had.
 *   • The first render is the real value with no cue; nothing counts up.
 *   • When the value changes, only the PRESENTATION moves: `data-changed`
 *     flips between "a" and "b" so a short CSS settle (opacity + 3px rise on
 *     the new figure) replays on every change. Reduced motion removes it.
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
  const previous = useRef(value);
  const [changes, setChanges] = useState(0);

  useEffect(() => {
    if (Object.is(previous.current, value)) return;
    previous.current = value;
    setChanges((count) => count + 1);
  }, [value]);

  const text = (format ?? ((n: number) => String(n)))(value);
  const cue = changes === 0 ? undefined : changes % 2 === 1 ? 'a' : 'b';

  return (
    <span className={`dcAnimatedNumber${className ? ` ${className}` : ''}`} data-value={text} data-changed={cue}>
      {text}
    </span>
  );
}
