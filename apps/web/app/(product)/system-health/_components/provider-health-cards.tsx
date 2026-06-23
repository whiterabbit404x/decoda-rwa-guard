import Link from 'next/link';
import { type ProviderEntry } from './types';
import { formatShortTime, statusBadgeClass, statusLabel } from './helpers';

type Props = {
  providers: ProviderEntry[];
  reportingSystems: number;
  monitoredSystems: number;
  truth: any;
};

export function ProviderHealthCards({
  providers,
  reportingSystems,
  monitoredSystems,
  truth,
}: Props) {
  const cards: ProviderEntry[] =
    providers.length > 0
      ? providers
      : [
          {
            name: 'Monitoring Systems',
            type: 'Provider Connectors',
            status:
              reportingSystems > 0
                ? 'healthy'
                : monitoredSystems > 0
                ? 'degraded'
                : 'unavailable',
            message:
              reportingSystems > 0
                ? `${reportingSystems} system${reportingSystems !== 1 ? 's' : ''} reporting`
                : 'No reporting systems',
            last_event: truth.last_poll_at ?? null,
            action: null,
          },
          {
            name: 'Evidence Provider',
            type: 'Evidence Export',
            status: truth.evidence_source_summary === 'live' ? 'healthy' : 'unavailable',
            message:
              truth.evidence_source_summary === 'live'
                ? 'Live evidence available'
                : 'Live evidence unavailable',
            last_event:
              truth.last_telemetry_at ?? truth.last_coverage_telemetry_at ?? null,
            action: null,
          },
        ];

  return (
    <section className="dataCard featureSection" style={{ marginTop: '1rem' }}>
      <div className="sectionHeader compact">
        <div>
          <p className="sectionEyebrow">External dependencies</p>
          <h2>Provider Health</h2>
        </div>
        <Link href="/integrations" style={{ fontSize: '0.85rem', color: '#6aa9ff', textDecoration: 'none' }}>
          View Integrations →
        </Link>
      </div>

      <div className="shProviderGrid">
        {cards.map((provider, index) => (
          <div key={`${provider.name}-${index}`} className="shProviderCard">
            <div className="shProviderCardHeader">
              <div>
                <p className="shProviderName">{provider.name}</p>
                <p className="shProviderType">{provider.type}</p>
              </div>
              <span
                className={statusBadgeClass(provider.status)}
                style={{ fontSize: '0.7rem', flexShrink: 0 }}
              >
                {statusLabel(provider.status)}
              </span>
            </div>
            <p className="shProviderSignal">{provider.message}</p>
            {provider.last_event && (
              <p className="shProviderTime">Last check: {formatShortTime(provider.last_event)}</p>
            )}
            {provider.action && <p className="shProviderAction">{provider.action}</p>}
          </div>
        ))}
      </div>
    </section>
  );
}
