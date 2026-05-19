$ErrorActionPreference = "Stop"

function Write-Info($msg) { Write-Host "[fix-before-push] $msg" }

# Run from repo root.
$dashboardPanel = "apps/web/app/dashboard-onboarding-panel.tsx"
$proofTest = "services/api/tests/test_proof_bundle_export.py"

if (-not (Test-Path $dashboardPanel)) {
  throw "Missing file: $dashboardPanel. Run this script from the decoda-rwa-guard repo root."
}

Write-Info "Fixing $dashboardPanel"
$text = Get-Content $dashboardPanel -Raw

# Remove duplicated Workspace line if Claude emitted it twice.
$text = [regex]::Replace(
  $text,
  "(?m)^(\s*)Workspace: <strong>\{workspaceName\}</strong>\r?\n\1Workspace: <strong>\{workspaceName\}</strong>\r?\n",
  "`$1Workspace: <strong>{workspaceName}</strong>`r`n"
)

# Avoid broken /targets navigation if the app does not have that route.
$targetsDir1 = "apps/web/app/(product)/targets"
$targetsDir2 = "apps/web/app/targets"
if ((-not (Test-Path $targetsDir1)) -and (-not (Test-Path $targetsDir2))) {
  $text = $text.Replace("href: '/targets',", "href: '/monitoring-sources',")
  $text = $text.Replace('href: "/targets",', 'href: "/monitoring-sources",')
  Write-Info "Replaced /targets href with /monitoring-sources"
} else {
  Write-Info "Keeping /targets because a targets route exists"
}

Set-Content -Path $dashboardPanel -Value $text -NoNewline -Encoding UTF8

if (Test-Path $proofTest) {
  Write-Info "Checking $proofTest for duplicated/corrupted proof bundle test tail"
  $proof = Get-Content $proofTest -Raw

  $canonicalTail = @'
def test_proof_bundle_includes_response_actions_and_detections_files(monkeypatch: pytest.MonkeyPatch) -> None:
    """Proof bundle must include response_actions.json, detections.json, audit_log.json."""
    fake_storage = _FakeStorage()
    monkeypatch.setattr(pilot, 'load_export_storage', lambda: fake_storage)
    connection = _CompleteChainConnection()
    pilot._generate_export_artifact(connection, workspace_id='ws-live', export_id='exp-1')

    payload = json.loads(fake_storage.content.decode('utf-8'))
    row = payload['rows'][0]
    assert 'response_actions.json' in row
    assert 'detections.json' in row
    assert 'audit_log.json' in row
    assert isinstance(row['response_actions.json'], list)
    assert isinstance(row['detections.json'], list)
    assert len(row['response_actions.json']) == 1
    assert len(row['detections.json']) == 1
'@

  $marker = "def test_proof_bundle_includes_response_actions_and_detections_files"
  $firstIndex = $proof.IndexOf($marker)
  if ($firstIndex -ge 0) {
    $prefix = $proof.Substring(0, $firstIndex).TrimEnd()
    $proof = $prefix + "`r`n`r`n" + $canonicalTail.Replace("`n", "`r`n") + "`r`n"
    Set-Content -Path $proofTest -Value $proof -NoNewline -Encoding UTF8
    Write-Info "Rewrote duplicated proof bundle test tail to one canonical test"
  } else {
    Write-Info "No duplicate proof bundle tail marker found; skipped"
  }
} else {
  Write-Info "Skipping missing backend test file: $proofTest"
}

Write-Info "Done. Review changes with: git diff --stat && git diff"
