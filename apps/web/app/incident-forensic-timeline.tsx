'use client';

import { useMemo } from 'react';

import { StatusPill } from './components/ui-primitives';
import {
  actorLabel,
  domainAccentVar,
  domainLabel,
  formatForensicDate,
  formatForensicTime,
  sortTimelineEvents,
  timelineDayHeadings,
  type ForensicLoadState,
  type IncidentEvidenceDomain,
  type IncidentTimelineEvent,
} from './incident-forensics-presentation';

/**
 * Screen 7 — forensic lifecycle timeline.
 *
 * Renders the deterministic event lifecycle the backend assembled from canonical
 * records: the chain observation, the operational reconciliation, the policy
 * evaluation and its decision, the response gate, incident creation, human
 * decisions, and evidence snapshot/sealing.
 *
 * Only stages with a REAL record appear. A stage from the reference design that
 * this incident has no record for is simply absent — nothing is back-filled to make
 * the flow look complete, and every timestamp is the canonical server timestamp the
 * source row carries (millisecond precision shown only where the record has it).
 */
export default function IncidentForensicTimeline({ events, load, partial, unreadable }: {
  events: readonly IncidentTimelineEvent[];
  load: ForensicLoadState;
  partial?: boolean;
  unreadable?: string[];
}) {
  // Ordering is by canonical server timestamp — never by array position.
  const ordered = useMemo(() => sortTimelineEvents(events), [events]);
  // A lifecycle can span midnight, so the day is stated whenever it changes; a
  // column of bare times would silently collapse several days into one burst.
  const dayHeadings = useMemo(() => timelineDayHeadings(ordered), [ordered]);

  if (load === 'idle' || load === 'loading') {
    return <p className="muted" style={{ fontSize: '0.85rem' }} aria-busy="true">Loading incident timeline…</p>;
  }
  if (load === 'unauthorized') {
    return (
      <p className="muted" style={{ fontSize: '0.85rem' }} role="alert">
        You do not have permission to view this incident&apos;s timeline in the current workspace.
      </p>
    );
  }
  if (load === 'not_found') {
    return <p className="muted" style={{ fontSize: '0.85rem' }} role="alert">This incident could not be found in the current workspace.</p>;
  }
  if (load === 'error') {
    return (
      <p className="muted" style={{ fontSize: '0.85rem' }} role="alert">
        Timeline unavailable — the incident history could not be read. No partial history is shown as a complete record.
      </p>
    );
  }
  if (ordered.length === 0) {
    return <p className="muted" style={{ fontSize: '0.85rem' }}>No lifecycle events have been recorded for this incident.</p>;
  }

  return (
    <div className="incidentForensicTimeline">
      {/* A source that could not be read is named. A history missing entries is
          never presented as the complete record. */}
      {partial ? (
        <p className="statusLine statusLine-warning" role="alert" style={{ margin: '0 0 0.6rem', fontSize: '0.8rem' }}>
          Partial history: {(unreadable ?? []).join(', ') || 'one or more sources'} could not be read.
        </p>
      ) : null}
      <ol className="incidentForensicTimelineList" aria-label="Incident forensic timeline">
        {ordered.map((event, index) => {
          const domain = (event.domain ?? null) as IncidentEvidenceDomain | null;
          const day = dayHeadings[index];
          return (
            <li key={event.id} className="incidentForensicTimelineItem">
              <span
                aria-hidden="true"
                className="incidentForensicTimelineMarker"
                style={{
                  background: domainAccentVar(domain),
                  boxShadow: `0 0 0 3px ${domain ? 'rgba(148,163,184,0.12)' : 'rgba(148,163,184,0.08)'}`,
                  // Stay level with the TIME row, which a day heading pushes down.
                  top: day ? '1.4rem' : '0.42rem',
                }}
              />
              <div style={{ minWidth: 0 }}>
                {day ? <p className="incidentTimelineDay">{day}</p> : null}
                <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'baseline', gap: '0.5rem' }}>
                  <time
                    dateTime={event.occurred_at ?? undefined}
                    title={formatForensicDate(event.occurred_at)}
                    style={{ fontFamily: 'monospace', fontSize: '0.76rem', color: 'var(--text-accent)', whiteSpace: 'nowrap' }}
                  >
                    {formatForensicTime(event.occurred_at)}
                  </time>
                  <span style={{ fontSize: '0.84rem', color: 'var(--text-primary)' }}>
                    {event.stage_label ?? event.title ?? 'Recorded event'}
                  </span>
                  {domain ? (
                    <span className="incidentDomainTag" style={{ color: domainAccentVar(domain), borderColor: 'var(--border)' }}>
                      {domainLabel(domain)}
                    </span>
                  ) : null}
                </div>
                {event.description && event.description !== event.stage_label ? (
                  <p className="muted" style={{ margin: '0.15rem 0 0', fontSize: '0.78rem', overflowWrap: 'anywhere' }}>
                    {event.description}
                  </p>
                ) : null}
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.4rem', marginTop: '0.25rem', alignItems: 'center' }}>
                  {/* Attribution is a fact: a system event is never attributed to a
                      person, and an AI layer is labelled recommend-only. */}
                  <span className="tableMeta" style={{ fontSize: '0.72rem' }}>{actorLabel(event)}</span>
                  {event.actor_type === 'user' ? <StatusPill label="Human action" variant="info" /> : null}
                  {event.related_entity_id ? (
                    <span className="tableMeta" style={{ fontFamily: 'monospace', fontSize: '0.68rem' }}
                      title={`${event.related_entity_type ?? 'record'}:${event.related_entity_id}`}>
                      {event.related_entity_type ?? 'record'}
                    </span>
                  ) : null}
                </div>
              </div>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
