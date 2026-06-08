from pathlib import Path


WORKFLOW_DIR = Path(__file__).resolve().parents[3] / '.github' / 'workflows'
WORKFLOW = WORKFLOW_DIR / 'release-attestation.yml'


def test_only_authoritative_release_workflow_exists() -> None:
    assert [path.name for path in WORKFLOW_DIR.glob('*.yml')] == ['release-attestation.yml']


def test_attestation_requires_all_security_gate_evidence() -> None:
    script = (Path(__file__).resolve().parents[3] / 'scripts' / 'release_attestation.py').read_text()
    for gate in ('dependency_scan', 'secret_scan', 'static_analysis', 'infrastructure_policy'):
        assert f'"{gate}"' in script
    assert 'missing required security gates' in script


def test_workflow_does_not_publish_mutable_latest_claims() -> None:
    workflow = WORKFLOW.read_text()
    assert '/latest' not in workflow
    assert 'overwrite: false' in workflow
    assert 'artifacts/release-attestations/' in workflow


def test_api_cryptography_dependency_includes_security_fix() -> None:
    requirements = (Path(__file__).resolve().parents[1] / 'requirements.txt').read_text()
    assert 'cryptography==46.0.5' in requirements
