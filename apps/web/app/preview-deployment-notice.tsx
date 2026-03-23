export default function PreviewDeploymentNotice() {
  return (
    <aside className="dataCard previewDeploymentNotice" role="alert" aria-live="assertive">
      <p className="eyebrow">Preview deployment warning</p>
      <h2>Preview URL is deployment-specific</h2>
      <p>
        This is a deployment-specific preview URL. Older preview URLs may not reflect the latest source.
      </p>
      <p>
        Before troubleshooting auth here, check <a href="/api/build-info">/api/build-info</a> to confirm the branch, commit, host,
        build timestamp, and backend API URL resolved for this exact preview deployment.
      </p>
    </aside>
  );
}
