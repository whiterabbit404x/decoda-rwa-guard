const PADDLE_SCRIPT_ORIGINS = ['https://cdn.paddle.com'];
const STRIPE_SCRIPT_ORIGINS = ['https://js.stripe.com'];

const BILLING_CONNECT_ORIGINS = [
  'https://*.paddle.com',
  'https://*.stripe.com',
];

const BILLING_FRAME_ORIGINS = [
  'https://checkout.paddle.com',
  'https://buy.paddle.com',
  'https://js.stripe.com',
  'https://hooks.stripe.com',
];

type ContentSecurityPolicyOptions = {
  development?: boolean;
};

export function buildContentSecurityPolicy(
  nonce: string,
  { development = false }: ContentSecurityPolicyOptions = {},
): string {
  const scriptSources = [
    "'self'",
    `'nonce-${nonce}'`,
    "'strict-dynamic'",
    ...PADDLE_SCRIPT_ORIGINS,
    ...STRIPE_SCRIPT_ORIGINS,
  ];
  const styleSources = ["'self'", `'nonce-${nonce}'`];

  if (development) {
    // React/Next.js development tooling requires eval and may inject unnonced
    // scripts and styles. These exceptions must never enter the production CSP.
    scriptSources.push("'unsafe-inline'", "'unsafe-eval'");
    styleSources.push("'unsafe-inline'");
  }

  return [
    "default-src 'self'",
    `script-src ${scriptSources.join(' ')}`,
    `style-src ${styleSources.join(' ')}`,
    "img-src 'self' data: blob: https:",
    "font-src 'self'",
    `connect-src 'self' ${BILLING_CONNECT_ORIGINS.join(' ')} wss: ws:`,
    `frame-src 'self' ${BILLING_FRAME_ORIGINS.join(' ')}`,
    "object-src 'none'",
    "frame-ancestors 'none'",
    "base-uri 'self'",
    "form-action 'self'",
  ].join('; ');
}
