import { healthIconClass, healthIconGlyph, overallLabel, statusBadgeClass } from './helpers';

type Props = {
  overallStatus: string;
  summaryText: string;
  primaryAction: string | null;
  contradictionFlags: string[];
  environment: string | null;
  gitCommit: string | null;
  frontendCommit?: string | null;
  generatedAt: string | null;
};

export function SystemHealthHero({
  overallStatus,
  summaryText,
  primaryAction,
  contradictionFlags,
  environment,
  gitCommit,
  frontendCommit,
  generatedAt,
}: Props) {
  return (
    <section className="dataCard shHero" data-status={overallStatus} aria-label="Overall system status">
      <div className="shHeroTop">
        {/* The badge beside it carries the status in words; the glyph is decoration. */}
        <div className={`${healthIconClass(overallStatus)} shHeroIcon`} aria-hidden="true">
          {healthIconGlyph(overallStatus)}
        </div>

        <div className="shHeroMain">
          <div className="shHeroBadgeRow">
            <span className={`${statusBadgeClass(overallStatus)} shHeroStatusBadge`}>
              {overallLabel(overallStatus)}
            </span>
            {environment && (
              <span className="statusBadge statusBadge-unavailable shEnvBadge">
                {environment}
              </span>
            )}
          </div>
          <p className="shHeroSummary">{summaryText}</p>
        </div>

        <div className="shHeroMeta">
          <p className="shMetaLabel">Last checked</p>
          <p className="shMetaValue">{generatedAt ?? 'Unavailable'}</p>
          {/* API + web build SHAs, both always shown. A missing SHA reads "unknown"
              (truthful) — never a fabricated value — so an operator can diagnose a
              stale Vercel/Railway deployment by comparing the two. */}
          <p className="shMetaCommit">api {gitCommit ?? 'unknown'}</p>
          <p className="shMetaCommit">web {frontendCommit ?? 'unknown'}</p>
        </div>
      </div>

      {primaryAction && (
        <div className="shActionCallout shActionCallout-warning">
          <span className="shActionCalloutIcon shActionCalloutIcon-warning" aria-hidden="true">⚠</span>
          <div>
            <strong className="shActionCalloutTitle shActionCalloutTitle-warning">
              Action required
            </strong>
            <p className="shActionCalloutBody shActionCalloutBody-warning">{primaryAction}</p>
          </div>
        </div>
      )}

      {contradictionFlags.length > 0 && (
        <div className="shActionCallout shActionCallout-danger">
          <span className="shActionCalloutIcon shActionCalloutIcon-danger" aria-hidden="true">✕</span>
          <div>
            <strong className="shActionCalloutTitle shActionCalloutTitle-danger">
              Runtime contradictions detected
            </strong>
            <ul className="shContradictionList">
              {contradictionFlags.map((flag: string) => (
                <li key={flag}>{flag.replace(/_/g, ' ')}</li>
              ))}
            </ul>
          </div>
        </div>
      )}
    </section>
  );
}
