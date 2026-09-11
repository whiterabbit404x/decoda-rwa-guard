"""Render the authoritative plan capability matrix as Markdown.

The matrix itself lives in ``services.api.app.entitlements.capability_matrix()``
and is DERIVED from the plan table and the lifecycle rules. This script only
formats it, so the document under ``docs/`` cannot drift from the engine that
enforces it — ``test_plan_entitlement_matrix_doc.py`` fails when it does.

Regenerate after changing a plan entitlement:

    python -m scripts.render_plan_matrix --write
"""

from __future__ import annotations

import argparse
from pathlib import Path

from scripts.bootstrap_paths import ensure_repo_on_path

ensure_repo_on_path()

from services.api.app import entitlements as ent  # noqa: E402

DOC_PATH = Path('docs/PLAN_ENTITLEMENT_MATRIX.md')

COLUMN_HEADERS: dict[str, str] = {
    ent.MATRIX_ACTIVE_PILOT: 'ACTIVE PILOT',
    ent.MATRIX_EXPIRED_PILOT: 'EXPIRED PILOT',
    ent.MATRIX_SCALE: 'SCALE',
    ent.MATRIX_ENTERPRISE: 'ENTERPRISE',
}

SOURCE_NOTES: dict[str, str] = {
    ent.SOURCE_FEATURE: 'feature entitlement',
    ent.SOURCE_LIMIT: 'plan limit',
    ent.SOURCE_LIFECYCLE: 'lifecycle (not plan-gated)',
    ent.SOURCE_ALWAYS: 'always available, expired tenants included',
    ent.SOURCE_AGREEMENT: 'commercial agreement, no software control',
}

PREAMBLE = """<!-- GENERATED FILE — do not edit by hand.
     Source: services/api/app/entitlements.py :: capability_matrix()
     Regenerate: python -m scripts.render_plan_matrix --write -->

# Plan entitlement matrix

The single authoritative statement of what each plan can do. Every cell below is
COMPUTED from `services/api/app/entitlements.py` — the same table the API,
the workers, and `GET /account/plan` read — so this document cannot promise a
capability the engine withholds, or withhold one the engine grants.

Two facts decide every row: the **plan** (Pilot / Scale / Enterprise) and the
**lifecycle state** (is the evaluation still running, is the tenant suspended).
That is why `ACTIVE PILOT` and `EXPIRED PILOT` are separate columns rather than
one "Pilot" column: an active evaluation is meant to exercise the production
security workflows, and an expired one keeps all of its data while losing the
ability to start new expensive work.

"""

FOOTER_TEMPLATE = """
## How to read the columns

* **ACTIVE PILOT** — a 30-day, approval-only evaluation. Bounded by 1 workspace,
  5 monitored contracts, 10 evidence packages, and recommend-only execution.
* **EXPIRED PILOT** — `evaluation_expires_at` has passed. Nothing is deleted and
  every read path still works; new monitoring, investigations, evidence packages,
  integrations and executions are refused with `PLAN_EVALUATION_EXPIRED`.
* **SCALE** — the ongoing paid production plan.
* **ENTERPRISE** — custom. Limits and features are widened per agreement through
  audited `entitlement_overrides` rows, never by being Enterprise alone.

A suspended organization behaves like an expired Pilot for every row decided by
lifecycle, regardless of plan.

## Automatic production execution

`NO` on every plan by default, including Enterprise. The product principle is
that AI recommends, a deterministic policy engine decides, and a human
authorizes. An Enterprise tenant that has signed off on autonomous execution
receives it through an explicit, audited `entitlement_overrides` entry.

## Declared but NOT enforced

These feature keys exist in the plan table and are reported by
`GET /account/plan`, but no code path reads them yet. They describe a commercial
intention, not a control, and must not be presented to a customer as a capability
their plan grants or withholds:

{unenforced}

## Grandfathered Pilots

An organization on the Pilot plan whose `evaluation_expires_at` is `NULL` never
expires: a missing deadline is not evidence of one, and inventing it would revoke
access the tenant was never told about. These predate the invitation lifecycle.
Newly approved Pilots always receive a real 30-day deadline.
"""


def render() -> str:
    rows = ent.capability_matrix()
    headers = ['Capability', *COLUMN_HEADERS.values(), 'Decided by']
    widths = [len(h) for h in headers]
    table: list[list[str]] = []
    for row in rows:
        decided = SOURCE_NOTES[row['source']]
        if not row['enforced']:
            decided += ' — NOT ENFORCED'
        cells = [
            str(row['capability']),
            *[str(row[column]) for column in ent.MATRIX_COLUMNS],
            decided,
        ]
        widths = [max(w, len(c)) for w, c in zip(widths, cells)]
        table.append(cells)

    def line(cells: list[str]) -> str:
        return '| ' + ' | '.join(c.ljust(w) for c, w in zip(cells, widths)) + ' |'

    lines = [line(headers), '| ' + ' | '.join('-' * w for w in widths) + ' |']
    lines.extend(line(cells) for cells in table)

    unenforced = '\n'.join(
        f'* `{key}`' for key in sorted(ent.UNENFORCED_FEATURES)
    ) or '* (none)'
    return PREAMBLE + '\n'.join(lines) + '\n' + FOOTER_TEMPLATE.format(unenforced=unenforced)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write', action='store_true', help='write docs/PLAN_ENTITLEMENT_MATRIX.md')
    args = parser.parse_args()
    content = render()
    if args.write:
        DOC_PATH.write_text(content, encoding='utf-8')
        print(f'wrote {DOC_PATH}')
    else:
        print(content)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
