import { type ComponentDetail, COMPONENT_META, COMPONENT_ORDER } from './types';
import { formatShortTime, statusBadgeClass, statusLabel } from './helpers';

type Props = {
  components: Record<string, ComponentDetail>;
};

export function HealthSummaryCards({ components }: Props) {
  return (
    <div className="fourColumnSection">
      {COMPONENT_ORDER.map((key) => {
        const meta = COMPONENT_META[key];
        const comp: ComponentDetail | undefined = components[key];
        // The endpoint was reachable (this component only renders in that case),
        // so a missing key means the backend omitted this specific check.
        const missingFromResponse = comp == null;
        const status = comp?.status ?? 'unavailable';
        return (
          <article key={key} className="dataCard shSummaryCard">
            <div className="shSummaryCardHeader">
              <p className="shSummaryCardTitle">{meta?.label ?? key}</p>
              <span
                className={statusBadgeClass(status)}
                style={{ fontSize: '0.7rem', padding: '0.2rem 0.55rem', flexShrink: 0 }}
              >
                {statusLabel(status)}
              </span>
            </div>

            {comp?.metric && <p className="shSummaryMetric">{comp.metric}</p>}

            <p className="shSummarySignal">
              {comp?.message ?? (missingFromResponse ? 'Component check missing from backend response.' : 'Unavailable')}
            </p>

            {comp?.age && <p className="shSummaryTime">Last: {comp.age}</p>}
            {!comp?.age && comp?.last_event && (
              <p className="shSummaryTime">{formatShortTime(comp.last_event)}</p>
            )}

            {comp?.action && <p className="shSummaryAction">{comp.action}</p>}
          </article>
        );
      })}
    </div>
  );
}
