from pathlib import Path


WORKFLOW = Path(__file__).resolve().parents[3] / '.github' / 'workflows' / 'ci-release-gates.yml'
SAFE_SETUP_TRIVY_SHA = '3fb12ec12f41e471780db15c232d5dd185dcb514'


def test_security_gate_pins_resolvable_setup_trivy_commit() -> None:
    workflow = WORKFLOW.read_text(encoding='utf-8')

    assert 'aquasecurity/setup-trivy@v0.2.4' not in workflow
    assert f'aquasecurity/setup-trivy@{SAFE_SETUP_TRIVY_SHA}' in workflow
    assert 'version: v0.69.3' in workflow


def test_final_readiness_requires_security_gate() -> None:
    workflow = WORKFLOW.read_text(encoding='utf-8')

    assert 'mandatory-security-supply-chain-gates:' in workflow
    assert 'needs: [paid-launch-readiness-gates, required-gates, mandatory-security-supply-chain-gates]' in workflow
    assert 'needs.mandatory-security-supply-chain-gates.result' in workflow
