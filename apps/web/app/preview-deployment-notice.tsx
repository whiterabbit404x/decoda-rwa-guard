import type { BuildInfo } from './build-info';

type PreviewDeploymentNoticeProps = {
  buildInfo: BuildInfo;
};

export default function PreviewDeploymentNotice({ buildInfo }: PreviewDeploymentNoticeProps) {
  return (
    <aside className="dataCard previewDeploymentNotice" role="note" aria-live="polite">
      <p className="eyebrow">Preview deployment</p>
      <h2>Preview URLs are deployment-specific</h2>
      <p>
        Old Vercel preview URLs can keep serving older auth UI even after newer commits deploy elsewhere. Before debugging, compare the
        commit SHA and branch on this page with the latest expected deployment.
      </p>
      <p>
        This preview currently reports <strong>{buildInfo.gitCommitShaShort ?? 'unknown commit'}</strong> on{' '}
        <strong>{buildInfo.gitBranch ?? 'unknown branch'}</strong>. Confirm those values first in{' '}
        <a href="/api/build-info">/api/build-info</a>.
      </p>
    </aside>
  );
}
