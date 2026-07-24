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
    <section className="dataCard shHero">
      <div className="shHeroTop">
        <div
          className={healthIconClass(overallStatus)}
          style={{ width: '3.5rem', height: '3.5rem', fontSize: '1.5rem', flexShrink: 0 }}
        >
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
          {gitCommit && <p className="shMetaCommit">api {gitCommit}</p>}
          {/* Frontend build SHA — lets an operator confirm the deployed web bundle
              is not stale (compare against the api commit above). */}
          <p className="shMetaCommit">web {frontendCommit ?? 'unknown'}</p>
        </div>
      </div>

      {primaryAction && (
        <div className="shActionCallout shActionCallout-warning">
          <span className="shActionCalloutIcon shActionCalloutIcon-warning">⚠</span>
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
          <span className="shActionCalloutIcon shActionCalloutIcon-danger">✕</span>
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
