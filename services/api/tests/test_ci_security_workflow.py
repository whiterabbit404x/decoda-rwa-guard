from pathlib import Path


WORKFLOW_DIR = Path(__file__).resolve().parents[3] / '.github' / 'workflows'
WORKFLOW = WORKFLOW_DIR / 'release-attestation.yml'


PR_CI_WORKFLOW = WORKFLOW_DIR / 'ci-pr.yml'

ALLOWED_WORKFLOWS = frozenset({'release-attestation.yml', 'ci-pr.yml'})


def test_only_authoritative_release_workflow_exists() -> None:
    present = {path.name for path in WORKFLOW_DIR.glob('*.yml')}
    unknown = present - ALLOWED_WORKFLOWS
    assert not unknown, f'Unexpected workflow file(s): {unknown}. Add to ALLOWED_WORKFLOWS if intentional.'
    assert 'release-attestation.yml' in present, 'Release attestation workflow must exist'


def test_pr_ci_workflow_exists() -> None:
    assert PR_CI_WORKFLOW.exists(), 'ci-pr.yml must exist for enterprise PR gates'


def test_pr_ci_workflow_does_not_require_production_secrets() -> None:
    workflow = PR_CI_WORKFLOW.read_text()
    for secret in ('RELEASE_PROBE_URL', 'RELEASE_PROBE_TOKEN', 'RELEASE_ATTESTATION_SIGNING_KEY'):
        assert f'secrets.{secret}' not in workflow, f'PR CI must not require production secret {secret}'


def test_pr_ci_workflow_runs_on_pull_request() -> None:
    workflow = PR_CI_WORKFLOW.read_text()
    assert 'pull_request' in workflow


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
