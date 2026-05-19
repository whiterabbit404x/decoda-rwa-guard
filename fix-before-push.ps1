# Fix known Claude patch issues before push.
# Run from repo root: powershell -ExecutionPolicy Bypass -File .\fix-before-push.ps1

$ErrorActionPreference = "Stop"

$panel = "apps\web\app\dashboard-onboarding-panel.tsx"
if (!(Test-Path $panel)) {
  throw "Missing file: $panel"
}

$text = Get-Content $panel -Raw

# Fix broken route if Claude used /targets.
$text = $text -replace "href: '/targets'", "href: '/monitoring-sources'"

# Remove duplicated Workspace line if present.
$text = $text -replace "Workspace: <strong>\{workspaceName\}</strong>\r?\n\s*Workspace: <strong>\{workspaceName\}</strong>", "Workspace: <strong>{workspaceName}</strong>"

Set-Content -Path $panel -Value $text -NoNewline

Write-Host "Fixed known dashboard onboarding issues."
Write-Host ""
Write-Host "Now run:"
Write-Host "git diff -- apps/web/app/dashboard-onboarding-panel.tsx"
Write-Host "cd apps/web"
Write-Host "npm test -- dashboard-truth-modal-rendering.spec.ts monitoring-truth-unified.spec.ts monitoring-status-contract.spec.ts onboarding-empty-state-truthfulness.spec.ts"
Write-Host "npm run typecheck"
Write-Host "cd ../.."
Write-Host "pytest services/api/tests/test_proof_bundle_export.py services/api/tests/test_assets_and_exports_foundations.py"
