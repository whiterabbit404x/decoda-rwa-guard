"""External canary command for the isolated monitoring pipeline path."""
from __future__ import annotations

import json

from services.api.app.monitoring_reliability import run_external_synthetic_check
from services.api.app.pilot import ensure_pilot_schema, pg_connection


def _traverse_known_event(_check_id: str, mark) -> None:
    # Each marker is persisted independently. Production deployments may replace this
    # traverser with an HTTP/RPC canary, but it must retain the dedicated synthetic tables.
    for stage in ("ingestion", "detection", "alerting", "incident_creation", "evidence_persistence"):
        mark(stage)


def main() -> None:
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        result = run_external_synthetic_check(connection, traverser=_traverse_known_event)
        connection.commit()
    print(json.dumps(result, sort_keys=True))
    if result["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
