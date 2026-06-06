const { runBuildEnvironmentValidation } = require('./build/vercel-build-validation');

runBuildEnvironmentValidation(process.env);

const isProd = process.env.NODE_ENV === 'production' || process.env.APP_MODE === 'production';

// Content-Security-Policy for Next.js
// unsafe-inline is required for Next.js inline styles/scripts injected at runtime.
// unsafe-eval is required for Next.js hot-reload in dev and some webpack chunks in prod.
// These are intentional trade-offs for Next.js compatibility; a nonce-based CSP would
// require custom Next.js middleware and is deferred.
const CSP = [
  "default-src 'self'",
  "script-src 'self' 'unsafe-inline' 'unsafe-eval'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob: https:",
  "font-src 'self'",
  "connect-src 'self' https://*.paddle.com https://*.stripe.com wss: ws:",
  "frame-src 'self' https://js.stripe.com https://hooks.stripe.com https://checkout.paddle.com https://buy.paddle.com",
  "frame-ancestors 'none'",
  "base-uri 'self'",
  "form-action 'self'",
].join('; ');

const securityHeaders = [
  { key: 'X-Content-Type-Options', value: 'nosniff' },
  // X-Frame-Options is kept for defense-in-depth alongside CSP frame-ancestors 'none'.
  { key: 'X-Frame-Options', value: 'DENY' },
  { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
  { key: 'Permissions-Policy', value: 'camera=(), microphone=(), geolocation=()' },
  { key: 'Content-Security-Policy', value: CSP },
];

if (isProd) {
  // HSTS: 2 years, includeSubDomains, preload. Only set in production to avoid
  // locking localhost/staging environments to HTTPS.
  securityHeaders.push({
    key: 'Strict-Transport-Security',
    value: 'max-age=63072000; includeSubDomains; preload',
  });
}

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  async headers() {
    return [
      {
        source: '/(.*)',
        headers: securityHeaders,
      },
    ];
  },
};

module.exports = nextConfig;
