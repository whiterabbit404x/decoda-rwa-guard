"""Alert Triage Agent domain (Screen 6 — Active Alerts).

Deterministic, workspace-scoped clustering of ACTIVE alerts into root-cause
groups, with a centralized severity/confidence policy, deterministic
recommendations, optional AI narrative enrichment, and a composed read model for
the Screen 6 UI. Nothing here creates, resolves, or mutates an alert — it is a
read-side grouping over alerts that already exist.
"""
