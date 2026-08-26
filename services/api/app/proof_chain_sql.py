"""ONE definition of the alert and incident proof chains, shared by every reader.

This module holds only SQL text and pure string builders — no framework imports —
so the runtime (``monitoring_runner``), the diagnostics/repair tooling
(``scripts/repair_live_rpc_proof_chain.py``) and the docs SQL can all ask exactly
the same provability question. A counter that disagrees with the definition below
is a false integrity warning, and a repair script that disagrees with it is a
destructive one.

Two questions live here, and they are NOT the same question:

  * "Can Decoda prove this OPEN ALERT?"        -> OPEN_ALERT_EVIDENCE_PROVABLE_SQL
  * "Can Decoda prove this OPEN INCIDENT?"     -> incident_proof_chain_count_sql

An incident is proven by a legitimately linked, same-workspace ALERT that is
itself provable. Its own status universe is wider than the alert counter's,
because an incident's provenance is HISTORICAL: see
INCIDENT_PROOF_CHAIN_ELIGIBLE_ALERT_STATUS_SQL.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Canonical open-alert provability predicate
# ---------------------------------------------------------------------------
# ONE definition of "Decoda can prove this open alert", correlated on the outer
# ``alerts a`` row and shared by every counter that asks the question. An alert is
# provable when a REAL evidence-bearing row exists in one of the evidence homes the
# shipped product actually writes:
#
#   1. alerts.detection_event_id -> detection_events -> telemetry_events
#      canonical lane, written by create_alert_from_detection_event (pilot.py).
#   2. alerts.detection_id / detections.linked_alert_id -> detections carrying
#      raw_evidence_json or a detection_evidence row. Legacy lane, written by
#      _upsert_alert (the QuickNode wallet-transfer path, monitoring_runner.py) and
#      monitoring_proof_chain (pilot.py).
#   3. asset_risk_findings.alert_id -> asset_risk_findings.evidence
#      domains/asset_risk/service.reconcile_findings. These alerts are raised from
#      deterministic asset-risk findings and structurally NEVER carry a chain
#      detection; their evidence lives on the finding row.
#   4. threat_detections.linked_alert_id -> threat_detection_evidence
#      domains/threat_detection/service.ensure_alert_for_detection.
#   5. alerts.analysis_run_id -> analysis_runs.response_payload
#      pilot.maybe_insert_alert.
#
# Lanes 3-5 are not a loosened threshold — they are evidence homes that shipped
# after the counter was written, so open alerts carrying genuine evidence were
# being counted as unprovable and degrading the whole workspace rollup to
# `limited` through contradiction_reason_overrides['alert_without_detection'].
#
# FAIL-CLOSED, deliberately:
#   * A LABEL IS NOT EVIDENCE. module_key ('asset_risk', 'threat_detection'),
#     source, source_service and alert_type prove nothing and appear in no lane;
#     an evidence-bearing ROW must exist.
#   * asset_risk_findings.evidence, analysis_runs.response_payload and
#     threat_detection_evidence.evidence_payload are all
#     ``JSONB NOT NULL DEFAULT '{}'::jsonb``, so ``IS NOT NULL`` is true of every
#     row and proves nothing. Each JSONB lane requires real content.
#   * Simulator-sourced threat detections are excluded: simulator data must never
#     be presented as customer evidence (CLAUDE.md).
#   * An alert matching no lane is still counted as unprovable, exactly as before.
#
# Every lane binds the alert's own workspace_id, so the predicate stays
# workspace-scoped and cross-tenant-safe wherever it is embedded.


def _non_empty_jsonb_sql(column: str) -> str:
    """SQL for "this JSONB column carries real content".

    The evidence columns are ``NOT NULL DEFAULT '{}'::jsonb``, so emptiness — not
    nullability — is what separates a row that carries evidence from a row that
    merely exists. Rejects SQL NULL, JSON ``null``, ``{}`` and ``[]``.
    """
    return (
        f"{column} IS NOT NULL"
        f" AND jsonb_typeof({column}) <> 'null'"
        f" AND {column} <> '{{}}'::jsonb"
        f" AND {column} <> '[]'::jsonb"
    )


OPEN_ALERT_CANONICAL_CHAIN_EVIDENCE_SQL = """EXISTS (
    SELECT 1
    FROM detection_events de
    JOIN telemetry_events te
      ON te.workspace_id = de.workspace_id
     AND te.id = de.telemetry_event_id
    WHERE de.workspace_id = a.workspace_id
      AND de.id = a.detection_event_id
)"""

OPEN_ALERT_LEGACY_DETECTION_EVIDENCE_SQL = """EXISTS (
    SELECT 1
    FROM detections d
    WHERE d.workspace_id = a.workspace_id
      AND (d.id = a.detection_id OR d.linked_alert_id = a.id)
      AND (
        d.raw_evidence_json IS NOT NULL
        OR EXISTS (
            SELECT 1
            FROM detection_evidence dev
            WHERE dev.workspace_id = d.workspace_id
              AND dev.detection_id = d.id
        )
      )
)"""

OPEN_ALERT_ASSET_RISK_EVIDENCE_SQL = f"""EXISTS (
    SELECT 1
    FROM asset_risk_findings f
    WHERE f.workspace_id = a.workspace_id
      AND f.alert_id = a.id
      AND ({_non_empty_jsonb_sql('f.evidence')})
)"""

OPEN_ALERT_THREAT_DETECTION_EVIDENCE_SQL = f"""EXISTS (
    SELECT 1
    FROM threat_detections td
    WHERE td.workspace_id = a.workspace_id
      AND td.linked_alert_id = a.id
      AND td.evidence_source <> 'simulator'
      AND EXISTS (
          SELECT 1
          FROM threat_detection_evidence tde
          WHERE tde.workspace_id = td.workspace_id
            AND tde.detection_id = td.id
            AND (
              tde.telemetry_id IS NOT NULL
              OR ({_non_empty_jsonb_sql('tde.evidence_payload')})
            )
      )
)"""

OPEN_ALERT_ANALYSIS_RUN_EVIDENCE_SQL = f"""EXISTS (
    SELECT 1
    FROM analysis_runs ar
    WHERE ar.id = a.analysis_run_id
      AND ar.workspace_id = a.workspace_id
      AND ({_non_empty_jsonb_sql('ar.response_payload')})
)"""

# The two chain-detection lanes on their own — the provability definition as it
# shipped before the asset-risk / threat-detection / analysis-run evidence homes
# existed. Kept named so the repair tooling and diagnostics can say precisely
# which half of the definition they mean.
OPEN_ALERT_CHAIN_EVIDENCE_PROVABLE_SQL = '\n    OR '.join((
    OPEN_ALERT_CANONICAL_CHAIN_EVIDENCE_SQL,
    OPEN_ALERT_LEGACY_DETECTION_EVIDENCE_SQL,
))

# The full canonical definition: provable by ANY supported evidence home.
OPEN_ALERT_EVIDENCE_PROVABLE_SQL = '\n    OR '.join((
    OPEN_ALERT_CANONICAL_CHAIN_EVIDENCE_SQL,
    OPEN_ALERT_LEGACY_DETECTION_EVIDENCE_SQL,
    OPEN_ALERT_ASSET_RISK_EVIDENCE_SQL,
    OPEN_ALERT_THREAT_DETECTION_EVIDENCE_SQL,
    OPEN_ALERT_ANALYSIS_RUN_EVIDENCE_SQL,
))


# ---------------------------------------------------------------------------
# Canonical open-incident proof chain
# ---------------------------------------------------------------------------
# An OPEN incident is proven when a same-workspace alert that Decoda can prove is
# legitimately linked to it. Two things differ from the open-alert counter, and
# both are product facts rather than relaxations:
#
# ALERT STATUS UNIVERSE. The alert counter asks about alerts that are open RIGHT
# NOW, so it is bounded by ('open','acknowledged','investigating'). An incident's
# provenance is HISTORICAL: the product deliberately keeps escalation and
# provenance after the originating alert is worked and resolved, so an incident
# routinely outlives its alert's active window. Reading the active-only universe
# made every such incident look orphaned the moment its alert was resolved.
# ``suppressed`` stays excluded: a suppressed alert is an explicit operator
# statement that the signal should not be acted on, so it may not carry proof.
#
# STATUS ALONE PROVES NOTHING. Eligibility is a filter, never a grant — the alert
# still has to satisfy OPEN_ALERT_EVIDENCE_PROVABLE_SQL. A resolved or
# investigating alert with no canonical evidence leaves its incident unprovable,
# exactly as before.
INCIDENT_PROOF_CHAIN_ELIGIBLE_ALERT_STATUS_SQL = "a.status <> 'suppressed'"

# The legitimate incident<->alert relationships in the current schema. All three
# are written by shipped code paths:
#   * alerts.incident_id        — set when an alert is escalated into an incident.
#   * incidents.source_alert_id — set when an incident is created FROM an alert
#     (migration 0043 / 0051 / 0054 / 0055).
#   * incidents.alert_id        — the FK-aligned column (migration 0074), backfilled
#     from source_alert_id and constrained to (alert_workspace_id, alert_id).
INCIDENT_PROOF_CHAIN_LINKAGE_SQL = """(
                pca.incident_id = i.id
                OR i.source_alert_id = pca.id
                OR i.alert_id = pca.id
            )"""


def incident_proof_chain_count_sql(
    *,
    alert_workspace_filter: str = '',
    incident_workspace_filter: str = '',
) -> str:
    """COUNT of OPEN incidents whose provenance Decoda can actually prove.

    ``alert_workspace_filter`` / ``incident_workspace_filter`` are the caller's
    own workspace predicates (``AND a.workspace_id = %s`` /
    ``AND i.workspace_id = %s::uuid`` …) appended verbatim. They are the ONLY
    interpolated text; everything else is fixed SQL.

    Tenant isolation does not depend on those filters: the linkage subquery binds
    ``pca.workspace_id = i.workspace_id``, so an alert can never prove another
    workspace's incident even when the counter runs unscoped.
    """
    return f'''
    WITH proof_chain_alerts AS (
        SELECT a.id, a.incident_id, a.workspace_id
        FROM alerts a
        WHERE {INCIDENT_PROOF_CHAIN_ELIGIBLE_ALERT_STATUS_SQL}
          AND (
            {OPEN_ALERT_EVIDENCE_PROVABLE_SQL}
          )
          {alert_workspace_filter}
    )
    SELECT COUNT(DISTINCT i.id) AS c
    FROM incidents i
    WHERE i.status IN ('open','acknowledged')
      AND EXISTS (
          SELECT 1
          FROM proof_chain_alerts pca
          WHERE pca.workspace_id = i.workspace_id
            AND {INCIDENT_PROOF_CHAIN_LINKAGE_SQL}
      )
      {incident_workspace_filter}
    '''
