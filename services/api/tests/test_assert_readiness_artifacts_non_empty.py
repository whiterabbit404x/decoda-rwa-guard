from __future__ import annotations

import json

from services.api.scripts import assert_readiness_artifacts_non_empty as script


def _write(path, payload):
    path.write_text(json.dumps(payload), encoding='utf-8')


def test_assert_readiness_artifacts_non_empty_passes_for_non_empty_arrays(tmp_path, monkeypatch):
    for filename in script.REQUIRED_ARRAY_ARTIFACTS:
        _write(tmp_path / filename, [{'id': f'{filename}-1'}])

    monkeypatch.setattr(
        'sys.argv',
        ['assert_readiness_artifacts_non_empty.py', '--artifacts-dir', str(tmp_path)],
    )

    assert script.main() == 0


def test_assert_readiness_artifacts_non_empty_fails_when_any_required_array_empty(tmp_path, monkeypatch):
    for filename in script.REQUIRED_ARRAY_ARTIFACTS:
        payload = [] if filename == 'runs.json' else [{'id': f'{filename}-1'}]
        _write(tmp_path / filename, payload)

    monkeypatch.setattr(
        'sys.argv',
        ['assert_readiness_artifacts_non_empty.py', '--artifacts-dir', str(tmp_path)],
    )

    assert script.main() == 1
