'use client';

import { FormEvent, useCallback, useEffect, useState } from 'react';
import { usePilotAuth } from 'app/pilot-auth-context';

type Destination = { id: string; name: string; destination_type: string; config: Record<string, unknown>; enabled: boolean };
type Policy = { id: string; name: string; severity_threshold: string; event_types: string[]; asset_ids: string[]; destination_ids: string[]; retry_schedule_seconds: number[]; suppression_seconds: number; escalation_after_seconds?: number; enabled: boolean };
type Attempt = { id: string; event_type: string; severity: string; status: string; destination_name?: string; attempt: number; error_message?: string; created_at: string };

export default function NotificationPoliciesPage() {
  const { apiUrl, authHeaders } = usePilotAuth();
  const [destinations, setDestinations] = useState<Destination[]>([]);
  const [policies, setPolicies] = useState<Policy[]>([]);
  const [attempts, setAttempts] = useState<Attempt[]>([]);
  const [message, setMessage] = useState('');

  const load = useCallback(async () => {
    const headers = authHeaders();
    const [configurationResponse, attemptsResponse] = await Promise.all([
      fetch(`${apiUrl}/integrations/notifications`, { headers, cache: 'no-store' }),
      fetch(`${apiUrl}/integrations/notifications/attempts`, { headers, cache: 'no-store' }),
    ]);
    if (!configurationResponse.ok || !attemptsResponse.ok) throw new Error('Unable to load notification configuration.');
    const configuration = await configurationResponse.json();
    const deliveryHistory = await attemptsResponse.json();
    setDestinations(configuration.destinations || []);
    setPolicies(configuration.policies || []);
    setAttempts(deliveryHistory.attempts || []);
  }, [apiUrl, authHeaders]);

  useEffect(() => { void load().catch((error) => setMessage(error.message)); }, [load]);

  async function createDestination(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const destinationType = String(data.get('destination_type'));
    const endpoint = String(data.get('endpoint'));
    const config = destinationType === 'email' ? { address: endpoint } : { url: endpoint, transport: 'https' };
    const response = await fetch(`${apiUrl}/integrations/notifications/destinations`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ name: data.get('name'), destination_type: destinationType, config, secret: data.get('secret') }),
    });
    if (!response.ok) throw new Error('Destination could not be saved.');
    event.currentTarget.reset(); setMessage('Destination saved.'); await load();
  }

  async function createPolicy(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const response = await fetch(`${apiUrl}/integrations/notifications/policies`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() }, body: JSON.stringify({
        name: data.get('name'), severity_threshold: data.get('severity_threshold'),
        asset_ids: String(data.get('asset_ids') || '').split(',').map((value) => value.trim()).filter(Boolean),
        event_types: String(data.get('event_types') || 'alert.created').split(',').map((value) => value.trim()).filter(Boolean),
        destination_ids: data.getAll('destination_ids'), retry_schedule_seconds: [30, 120, 600, 1800],
        suppression_seconds: Number(data.get('suppression_seconds') || 0),
        escalation_after_seconds: Number(data.get('escalation_after_seconds') || 0) || null,
        escalation_destination_ids: data.getAll('escalation_destination_ids'), enabled: true,
      }),
    });
    if (!response.ok) throw new Error('Policy could not be saved.');
    event.currentTarget.reset(); setMessage('Notification policy saved.'); await load();
  }

  async function acknowledge(attemptId: string) {
    await fetch(`${apiUrl}/integrations/notifications/attempts/${attemptId}/acknowledge`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() }, body: JSON.stringify({ note: 'Acknowledged from notification operations.' }),
    });
    await load();
  }

  return <main className="pageStack">
    <header><p className="eyebrow">Operations</p><h1>Notification policies</h1><p>Route live workspace events by severity, asset, and event type. Configure retries, suppression, escalation, and acknowledgement without support intervention.</p></header>
    {message ? <p role="status" className="statusBanner">{message}</p> : null}
    <section className="panel"><h2>Destinations</h2><form onSubmit={(event) => void createDestination(event).catch((error) => setMessage(error.message))} className="formGrid">
      <label>Name<input name="name" required placeholder="Primary on-call" /></label>
      <label>Type<select name="destination_type" defaultValue="pagerduty"><option value="pagerduty">PagerDuty</option><option value="slack">Slack</option><option value="teams">Microsoft Teams</option><option value="email">Email</option><option value="siem_syslog">SIEM / syslog</option><option value="webhook">Workspace webhook</option></select></label>
      <label>Endpoint or email<input name="endpoint" required placeholder="https://… or oncall@example.com" /></label>
      <label>Secret / routing key<input name="secret" type="password" autoComplete="new-password" /></label><button type="submit">Add destination</button>
    </form><ul>{destinations.map((destination) => <li key={destination.id}><strong>{destination.name}</strong> · {destination.destination_type} · {destination.enabled ? 'enabled' : 'disabled'}</li>)}</ul></section>
    <section className="panel"><h2>Routing and escalation</h2><form onSubmit={(event) => void createPolicy(event).catch((error) => setMessage(error.message))} className="formGrid">
      <label>Policy name<input name="name" required placeholder="Critical asset response" /></label><label>Minimum severity<select name="severity_threshold" defaultValue="high"><option>info</option><option>low</option><option>medium</option><option>high</option><option>critical</option></select></label>
      <label>Asset IDs<input name="asset_ids" placeholder="asset-1, asset-2 (blank means all)" /></label><label>Event types<input name="event_types" defaultValue="alert.created" /></label>
      <label>Suppress duplicates (seconds)<input name="suppression_seconds" type="number" min="0" defaultValue="300" /></label><label>Escalate after (seconds)<input name="escalation_after_seconds" type="number" min="0" defaultValue="900" /></label>
      <fieldset><legend>Destinations</legend>{destinations.map((destination) => <label key={destination.id}><input type="checkbox" name="destination_ids" value={destination.id} /> {destination.name}</label>)}</fieldset>
      <fieldset><legend>Escalation destinations</legend>{destinations.map((destination) => <label key={destination.id}><input type="checkbox" name="escalation_destination_ids" value={destination.id} /> {destination.name}</label>)}</fieldset><button type="submit">Save policy</button>
    </form><ul>{policies.map((policy) => <li key={policy.id}><strong>{policy.name}</strong> · {policy.severity_threshold}+ · {policy.event_types.join(', ')} · retries {policy.retry_schedule_seconds.join('s, ')}s</li>)}</ul></section>
    <section className="panel"><h2>Delivery attempts and acknowledgements</h2><div className="tableWrap"><table><thead><tr><th>Created</th><th>Event</th><th>Destination</th><th>Status</th><th>Attempts</th><th>Action</th></tr></thead><tbody>{attempts.map((attempt) => <tr key={attempt.id}><td>{new Date(attempt.created_at).toLocaleString()}</td><td>{attempt.severity} · {attempt.event_type}</td><td>{attempt.destination_name || 'Removed destination'}</td><td title={attempt.error_message}>{attempt.status}</td><td>{attempt.attempt}</td><td>{attempt.status !== 'acknowledged' ? <button type="button" onClick={() => void acknowledge(attempt.id)}>Acknowledge</button> : 'Acknowledged'}</td></tr>)}</tbody></table></div></section>
  </main>;
}
