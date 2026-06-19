export async function register() {
  if (process.env.NEXT_RUNTIME === 'nodejs') {
    const commitSha =
      process.env.RAILWAY_GIT_COMMIT_SHA ??
      process.env.VERCEL_GIT_COMMIT_SHA ??
      process.env.NEXT_PUBLIC_VERCEL_GIT_COMMIT_SHA ??
      'unavailable';
    console.log(`startup_git_commit_sha service_role=web git_commit_sha=${commitSha}`);
  }
}
