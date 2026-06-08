const { runBuildEnvironmentValidation } = require('./build/vercel-build-validation');

runBuildEnvironmentValidation(process.env);

const isProd = process.env.NODE_ENV === 'production' || process.env.APP_MODE === 'production';

// Per-request Content-Security-Policy headers are generated in proxy.ts so
// Next.js can apply a unique nonce to framework and application scripts.
const securityHeaders = [
  { key: 'X-Content-Type-Options', value: 'nosniff' },
  // X-Frame-Options is kept for defense-in-depth alongside CSP frame-ancestors 'none'.
  { key: 'X-Frame-Options', value: 'DENY' },
  { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
  { key: 'Permissions-Policy', value: 'camera=(), microphone=(), geolocation=()' },
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
