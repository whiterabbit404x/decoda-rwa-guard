'use client';

import { useMemo, type CSSProperties } from 'react';

import { StatusPill } from './components/ui-primitives';
import {
  EVIDENCE_DOMAINS,
  actorLabel,
  domainAccentVar,
  domainBorderVar,
  domainLabel,
  domainSurfaceVar,
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
export default function IncidentForensicTimeline({ events, load, partial, unreadable, undatedEvents }: {
  events: readonly IncidentTimelineEvent[];
  load: ForensicLoadState;
  partial?: boolean;
  unreadable?: string[];
  /** Persisted records the backend could not place in time, because their source
   *  timestamp column is null. Reported, never silently dropped. */
  undatedEvents?: number;
}) {
  // Ordering is by canonical server timestamp — never by array position.
  const ordered = useMemo(() => sortTimelineEvents(events), [events]);
  // A lifecycle can span midnight, so the day is stated whenever it changes; a
  // column of bare times would silently collapse several days into one burst.
  const dayHeadings = useMemo(() => timelineDayHeadings(ordered), [ordered]);

  // The marker types actually present on this chronology — the legend explains
  // only what is drawn, never a domain this incident has no record for.
  const presentDomains = useMemo(
    () => EVIDENCE_DOMAINS.filter((domain) => ordered.some((event) => event.domain === domain)),
    [ordered],
  );

  if (load === 'idle' || load === 'loading') {
    return <p className="muted incidentTimelineState" aria-busy="true">Loading incident timeline…</p>;
  }
  if (load === 'unauthorized') {
    return (
      <p className="muted incidentTimelineState" role="alert">
        You do not have permission to view this incident&apos;s timeline in the current workspace.
      </p>
    );
  }
  if (load === 'not_found') {
    return <p className="muted incidentTimelineState" role="alert">This incident could not be found in the current workspace.</p>;
  }
  if (load === 'error') {
    return (
      <p className="muted incidentTimelineState" role="alert">
        Timeline unavailable — the incident history could not be read. No partial history is shown as a complete record.
      </p>
    );
  }
  if (ordered.length === 0) {
    return <p className="muted incidentTimelineState">No lifecycle events have been recorded for this incident.</p>;
  }

  return (
    <div className="incidentForensicTimeline">
      {/* What is on the rail: the count of events placed on it, and a key for the
          marker types it actually draws. Both are read from the events below. */}
      <div className="incidentTimelineHeader">
        <div className="incidentTimelineHeading">
          <p className="incidentTimelineEyebrow">Lifecycle chronology</p>
          <p className="incidentTimelineCount">
            {ordered.length} {ordered.length === 1 ? 'event' : 'events'} on this chronology
          </p>
        </div>
        {presentDomains.length > 0 ? (
          <ul className="incidentTimelineLegend" aria-label="Event domains on this timeline">
            {presentDomains.map((domain) => (
              <li key={domain} className="incidentTimelineLegendItem" style={domainStyle(domain)}>
                <span aria-hidden="true" className="incidentTimelineLegendMark">
                  <TimelineGlyph domain={domain} actorType={null} />
                </span>
                {domainLabel(domain)}
              </li>
            ))}
          </ul>
        ) : null}
      </div>
      {/* A source that could not be read is named. A history missing entries is
          never presented as the complete record. */}
      {partial ? (
        <p className="statusLine statusLine-warning incidentTimelineNotice" role="alert">
          Partial history: {(unreadable ?? []).join(', ') || 'one or more sources'} could not be read.
        </p>
      ) : null}
      {/* A record with no canonical timestamp cannot be placed in a chronology —
          positioning it would state an order the data does not support. It is
          withheld here and stated, so a filtered timeline is never presented as
          the complete set of records. The evidence directory still lists it. */}
      {(undatedEvents ?? 0) > 0 ? (
        <p className="muted incidentTimelineUndated">
          {undatedEvents} recorded {undatedEvents === 1 ? 'event carries' : 'events carry'} no canonical
          timestamp and {undatedEvents === 1 ? 'is' : 'are'} not placed on this chronology. They remain in the
          evidence directory.
        </p>
      ) : null}
      <ol className="incidentForensicTimelineList" aria-label="Incident forensic timeline">
        {ordered.map((event, index) => {
          const domain = (event.domain ?? null) as IncidentEvidenceDomain | null;
          const day = dayHeadings[index];
          return (
            <li
              key={event.id}
              className="incidentForensicTimelineItem"
              data-domain={domain ?? 'none'}
              data-actor={event.actor_type ?? 'system'}
              style={domainStyle(domain)}
            >
              {/* The calendar day is stated on the rail whenever it changes. */}
              {day ? <p className="incidentTimelineDay"><span className="incidentTimelineDayLabel">{day}</span></p> : null}
              <div className="incidentTimelineEntry">
                <time
                  className="incidentTimelineTime"
                  dateTime={event.occurred_at ?? undefined}
                  title={formatForensicDate(event.occurred_at)}
                >
                  {formatForensicTime(event.occurred_at)}
                </time>
                {/* Typed marker: the glyph names the evidence domain (or, for an
                    unclassified record, the actor), so the rail is legible without
                    relying on colour alone. */}
                <span aria-hidden="true" className="incidentForensicTimelineMarker">
                  <TimelineGlyph domain={domain} actorType={event.actor_type ?? null} />
                </span>
                <div className="incidentTimelineCard">
                  <div className="incidentTimelineCardHead">
                    <span className="incidentTimelineTitle">
                      {event.stage_label ?? event.title ?? 'Recorded event'}
                    </span>
                    {domain ? <span className="incidentDomainTag incidentTimelineDomainTag">{domainLabel(domain)}</span> : null}
                  </div>
                  {event.description && event.description !== event.stage_label ? (
                    <p className="incidentTimelineDescription">{event.description}</p>
                  ) : null}
                  <div className="incidentTimelineMeta">
                    {/* Attribution is a fact: a system event is never attributed to a
                        person, and an AI layer is labelled recommend-only. */}
                    <span className="incidentTimelineActor">{actorLabel(event)}</span>
                    {event.actor_type === 'user' ? <StatusPill label="Human action" variant="info" /> : null}
                    {event.related_entity_id ? (
                      <span className="incidentTimelineEntity"
                        title={`${event.related_entity_type ?? 'record'}:${event.related_entity_id}`}>
                        {event.related_entity_type ?? 'record'}
                      </span>
                    ) : null}
                  </div>
                </div>
              </div>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

/** The domain's own semantic tokens, handed to CSS as custom properties so the
 *  colour mapping stays the single one in incident-forensics-presentation. */
function domainStyle(domain: IncidentEvidenceDomain | null): CSSProperties {
  return {
    '--incident-tl-accent': domainAccentVar(domain),
    '--incident-tl-surface': domainSurfaceVar(domain),
    '--incident-tl-border': domainBorderVar(domain),
  } as CSSProperties;
}

/** One 12px line glyph per evidence domain. An unclassified record gets a plain
 *  dot (or an AI mark when the AI layer is the recorded actor) — never a domain
 *  glyph it was not classified into. */
function TimelineGlyph({ domain, actorType }: { domain: IncidentEvidenceDomain | null; actorType: string | null }) {
  const common = {
    width: 12, height: 12, viewBox: '0 0 16 16', fill: 'none', stroke: 'currentColor',
    strokeWidth: 1.6, strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const, focusable: false,
  };
  switch (domain) {
    case 'ON_CHAIN':
      return <svg {...common}><path d="M8 1.75 13.5 4.9v6.2L8 14.25 2.5 11.1V4.9z" /><path d="M2.5 4.9 8 8l5.5-3.1M8 8v6.25" /></svg>;
    case 'OPERATIONAL':
      return <svg {...common}><ellipse cx="8" cy="4" rx="5" ry="2" /><path d="M3 4v8c0 1.1 2.24 2 5 2s5-.9 5-2V4M3 8c0 1.1 2.24 2 5 2s5-.9 5-2" /></svg>;
    case 'POLICY':
      return <svg {...common}><path d="M8 1.75 13.25 3.6v3.9c0 3.1-2.2 5.6-5.25 6.75C4.95 13.1 2.75 10.6 2.75 7.5V3.6z" /><path d="m5.75 7.9 1.6 1.6 2.9-3" /></svg>;
    case 'HUMAN_ACTION':
      return <svg {...common}><circle cx="8" cy="5.25" r="2.6" /><path d="M2.9 14c.55-2.6 2.6-4.1 5.1-4.1s4.55 1.5 5.1 4.1" /></svg>;
    default:
      if (actorType === 'ai') {
        return <svg {...common}><path d="M8 2.25 9.3 6.7 13.75 8 9.3 9.3 8 13.75 6.7 9.3 2.25 8 6.7 6.7z" /></svg>;
      }
      return <svg {...common}><circle cx="8" cy="8" r="2.25" fill="currentColor" stroke="none" /></svg>;
  }
}
