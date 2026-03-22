export default function PreviewDeploymentNotice() {
  return (
    <aside className="dataCard previewDeploymentNotice" role="note" aria-live="polite">
      <p className="eyebrow">Preview deployment</p>
      <h2>Preview environment detected</h2>
      <p>
        This Vercel preview build uses deployment-specific runtime config. If sign-in or sign-up fails here while production still works,
        check <a href="/api/build-info">/api/build-info</a> first to confirm the branch, commit, live-mode flag, and backend API URL resolved for this preview.
      </p>
    </aside>
  );
}
