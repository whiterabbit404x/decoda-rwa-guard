// Decoda Assets / Decoda Vault product events on the Integrations page. Every state is
// derived from what the backend recorded (GET /integrations/decoda); none is assumed.
// "Receiving" means signed events actually arrived — never that monitoring is healthy.
import type { PillVariant } from '../components/ui-primitives';

export type DecodaProductState =
  | 'not_configured'
  | 'no_platform_organization'
  | 'not_linked'
  | 'linked_elsewhere'
  | 'awaiting_first_event'
  | 'receiving';

export type DecodaProductStatus = {
  producer: 'decoda-assets' | 'decoda-vault';
  label: string;
  state: DecodaProductState;
  evidence_label: string;
  linked_here: boolean;
  linked_workspace: string | null;
  linked_at: string | null;
  events_received: number;
  alerts_created: number;
  last_event_at: string | null;
  unrouted_events: number;
  signals: {
    configured: boolean;
    delivered: number;
    pending: number;
    failed: number;
    dead: number;
    not_configured: number;
  };
};

export type DecodaProductsView = {
  organization: { linked: boolean; has_platform_organization: boolean };
  can_manage: boolean;
  products: DecodaProductStatus[];
};

export const DECODA_PRODUCT_STATE: Record<DecodaProductState, { label: string; variant: PillVariant; detail: string }> = {
  not_configured: {
    label: 'Not configured',
    variant: 'neutral',
    detail: 'This Guard deployment has no shared secret for this product, so it accepts none of its events.',
  },
  no_platform_organization: {
    label: 'No Decoda organization',
    variant: 'warning',
    detail: 'This organization is not linked to a Decoda platform organization, so product events cannot be attributed to it.',
  },
  not_linked: {
    label: 'Not routed',
    variant: 'warning',
    detail: 'Events from this product are recorded but not analysed until an organization admin routes them to a workspace.',
  },
  linked_elsewhere: {
    label: 'Routed to another workspace',
    variant: 'neutral',
    detail: "This product's events are analysed in another workspace of your organization.",
  },
  awaiting_first_event: {
    label: 'Awaiting first event',
    variant: 'info',
    detail: 'Routed to this workspace. No signed event has arrived yet.',
  },
  receiving: {
    label: 'Receiving events',
    variant: 'success',
    detail: 'Signed testnet events are arriving. They create alerts labelled "Testnet product event"; they are not live telemetry.',
  },
};

export function signalSummary(signals: DecodaProductStatus['signals']): { text: string; variant: PillVariant } {
  if (!signals.configured) {
    return { text: 'Signals back to this product are not configured', variant: 'neutral' };
  }
  if (signals.dead > 0 || signals.failed > 0) {
    return { text: `${signals.dead + signals.failed} signal(s) not delivered`, variant: 'danger' };
  }
  if (signals.pending > 0) return { text: `${signals.pending} signal(s) pending`, variant: 'warning' };
  if (signals.delivered > 0) return { text: `${signals.delivered} signal(s) delivered`, variant: 'success' };
  return { text: 'No signal sent yet', variant: 'neutral' };
}

const UNKNOWN_STATE = { label: 'Status unknown', variant: 'neutral' as PillVariant, detail: 'Guard reported a state this page does not know.' };

export function stateFor(state: string): { label: string; variant: PillVariant; detail: string } {
  return (DECODA_PRODUCT_STATE as Record<string, { label: string; variant: PillVariant; detail: string }>)[state] ?? UNKNOWN_STATE;
}

/** The backend's answer, or null when it is not the shape this page renders (shown as unavailable). */
export function parseDecodaProductsView(payload: unknown): DecodaProductsView | null {
  if (!payload || typeof payload !== 'object') return null;
  const view = payload as Partial<DecodaProductsView>;
  if (!Array.isArray(view.products) || !view.organization || typeof view.organization !== 'object') return null;
  const products = view.products.filter(
    (p): p is DecodaProductStatus =>
      !!p && typeof p === 'object' && typeof p.producer === 'string' && typeof p.state === 'string' && !!p.signals && typeof p.signals === 'object',
  );
  if (products.length !== view.products.length) return null;
  return { organization: view.organization, can_manage: view.can_manage === true, products };
}
