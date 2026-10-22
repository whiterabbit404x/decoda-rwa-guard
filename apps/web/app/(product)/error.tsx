'use client';

import { ScreenError } from '../components/screen-states';

/**
 * A product screen that throws while rendering fails here, inside the shell:
 * the sidebar, workspace and runtime banner stay usable, and the failure is
 * stated as a failure rather than as an empty screen. Before this boundary
 * existed the error reached the root and replaced the whole application.
 */
export default function ProductScreenError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return <ScreenError digest={error.digest} onRetry={reset} />;
}
