const { runBuildEnvironmentValidation } = require('./build/vercel-build-validation');

runBuildEnvironmentValidation(process.env);

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
};

module.exports = nextConfig;
