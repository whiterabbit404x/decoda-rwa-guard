import { type HealthEvent } from './types';
import { formatDateTime, severityBadgeClass } from './helpers';

type Props = {
  events: HealthEvent[];
};

export function HealthTimeline({ events }: Props) {
  return (
    <section className="dataCard featureSection shSection">
      <div className="sectionHeader compact">
        <div>
          <p className="sectionEyebrow">Recent activity</p>
          <h2>Incident &amp; Health Timeline</h2>
        </div>
      </div>

      {events.length > 0 ? (
        <div className="shTimeline">
          {events.map((event, index) => (
            <div key={`${event.component}-${index}`} className="shTimelineItem" data-severity={event.severity}>
              {/* Severity hue comes from data-severity in CSS (critical/high red,
                  medium amber, anything else neutral) — no inline style, which the
                  production CSP blocks on server-rendered markup. */}
              <div className="shTimelineDot" data-severity={event.severity} aria-hidden="true" />
              <div className="shTimelineContent">
                <div className="shTimelineHeader">
                  <span className="shTimelineTime">{formatDateTime(event.time)}</span>
                  <span className="shTimelineComponent">{event.component}</span>
                  <span className={`${severityBadgeClass(event.severity)} shBadgeSm`}>
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
          {/* Neutral, not green: an empty event log is an absence of records,
              not a health verdict. */}
          <div className="shEmptyIcon" aria-hidden="true">–</div>
          <p className="shEmptyText">No recent health events.</p>
          <p className="shEmptySubtext">
            The system has been operating without recorded health events.
          </p>
        </div>
      )}
    </section>
  );
}
