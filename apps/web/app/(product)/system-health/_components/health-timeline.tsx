import { type HealthEvent } from './types';
import { formatDateTime, severityBadgeClass } from './helpers';

type Props = {
  events: HealthEvent[];
  noSystemHealthData: boolean;
};

const SEVERITY_COLORS: Record<string, string> = {
  critical: '#f87171',
  high: '#f87171',
  medium: '#fbbf24',
};

export function HealthTimeline({ events, noSystemHealthData }: Props) {
  return (
    <section className="dataCard featureSection" style={{ marginTop: '1rem' }}>
      <div className="sectionHeader compact">
        <div>
          <p className="sectionEyebrow">Recent activity</p>
          <h2>Incident &amp; Health Timeline</h2>
        </div>
      </div>

      {events.length > 0 ? (
        <div className="shTimeline">
          {events.map((event, index) => (
            <div key={`${event.component}-${index}`} className="shTimelineItem">
              <div
                className="shTimelineDot"
                style={{
                  background: SEVERITY_COLORS[event.severity] ?? 'rgba(148, 163, 184, 0.4)',
                }}
              />
              <div className="shTimelineContent">
                <div className="shTimelineHeader">
                  <span className="shTimelineTime">{formatDateTime(event.time)}</span>
                  <span className="shTimelineComponent">{event.component}</span>
                  <span
                    className={severityBadgeClass(event.severity)}
                    style={{ fontSize: '0.7rem' }}
                  >
                    {event.severity}
                  </span>
                </div>
                <p className="shTimelineEvent">{event.event}</p>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="shEmptyState">
          <div className="shEmptyIcon shEmptyIcon-ok">✓</div>
          <p className="shEmptyText">No recent health events.</p>
          <p className="shEmptySubtext">
            {noSystemHealthData
              ? 'Backend health endpoint could not be reached.'
              : 'The system has been operating without recorded health events.'}
          </p>
        </div>
      )}
    </section>
  );
}
