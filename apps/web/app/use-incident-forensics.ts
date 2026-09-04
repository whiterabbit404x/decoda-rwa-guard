'use client';

import { useCallback, useEffect, useState } from 'react';

import { usePilotAuth } from './pilot-auth-context';
import {
  loadStateFor,
  type ForensicLoadState,
  type IncidentCaseSummary,
  type IncidentEvidenceArtifact,
  type IncidentEvidenceCounts,
  type IncidentEvidencePackage,
  type IncidentForensicSnapshot,
  type IncidentPolicyEvaluation,
} from './incident-forensics-presentation';

// Same-origin proxy base — the Incidents UI never calls the backend directly (the
// browser only sees NEXT_PUBLIC_API_URL, often unset in production). Every call
// goes through the Next.js /api/* proxy, the same transport the rest of Screen 7 uses.
const API_PROXY_BASE = '/api';

/** The `GET /incidents/{id}/evidence` payload, in full. */
export type IncidentEvidenceResponse = {
  incident_id?: string;
  event_id?: string | null;
  incident?: {
    incident_id?: string;
    reference?: string | null;
    title?: string | null;
    severity?: string | null;
    status?: string | null;
    asset_id?: string | null;
    asset_label?: string | null;
    target_id?: string | null;
    detection_category?: string | null;
    detection_type?: string | null;
    detection_title?: string | null;
    opened_at?: string | null;
    updated_at?: string | null;
  };
  counts?: IncidentEvidenceCounts;
  snapshot?: IncidentForensicSnapshot;
  evidence_package?: IncidentEvidencePackage;
  policy_evaluations?: IncidentPolicyEvaluation[];
  case_summary?: IncidentCaseSummary;
  artifacts?: IncidentEvidenceArtifact[];
  truncated?: boolean;
  partial?: boolean;
  unreadable?: string[];
};

export type IncidentEvidenceState = {
  data: IncidentEvidenceResponse | null;
  load: ForensicLoadState;
  refresh: () => void;
};

/**
 * One fetch of an incident's forensic evidence record, shared by every Screen 7
 * surface that needs it.
 *
 * The Case File header, the Overview summary and the Evidence directory all read
 * the SAME payload — the case summary, the domain counts, the snapshot integrity
 * state and the artifact list arrive together. Hoisting the fetch here means one
 * request per selected incident instead of one per consumer, and it makes it
 * structurally impossible for the header to describe a different evidence state
 * than the directory beneath it.
 *
 * A response for a previously selected incident can never land on the current
 * one: every request carries an AbortController plus a cancelled guard.
 */
export function useIncidentEvidence(incidentId: string): IncidentEvidenceState {
  const { authHeaders } = usePilotAuth();
  const [data, setData] = useState<IncidentEvidenceResponse | null>(null);
  const [load, setLoad] = useState<ForensicLoadState>('idle');
  const [reloadToken, setReloadToken] = useState(0);

  const refresh = useCallback(() => setReloadToken((token) => token + 1), []);

  useEffect(() => {
    if (!incidentId) {
      setData(null);
      setLoad('idle');
      return;
    }
    let cancelled = false;
    const controller = new AbortController();
    setLoad('loading');
    void fetch(`${API_PROXY_BASE}/incidents/${encodeURIComponent(incidentId)}/evidence`, {
      headers: authHeaders(),
      cache: 'no-store',
      signal: controller.signal,
    })
      .then(async (res) => {
        if (cancelled) return;
        if (!res.ok) {
          setData(null);
          setLoad(loadStateFor(res.status, false));
          return;
        }
        const json = (await res.json()) as IncidentEvidenceResponse;
        if (cancelled) return;
        setData(json);
        setLoad(loadStateFor(res.status, (json.artifacts ?? []).length > 0));
      })
      .catch(() => {
        if (cancelled) return;
        setData(null);
        setLoad('error');
      });
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [authHeaders, incidentId, reloadToken]);

  return { data, load, refresh };
}
