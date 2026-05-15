# Run from repo root: F:\Blockchain_Security3\decoda-rwa-guard
# Purpose: fix the exact corrupted placeholder lines still failing in Vercel.
# Targets:
#   apps/web/app/dashboard-executive-summary.tsx line around 195
#   apps/web/app/threat-monitoring-panel.tsx line around 369

$ErrorActionPreference = "Stop"

# 1) Fix dashboard monitored systems placeholder
$path = "apps/web/app/dashboard-executive-summary.tsx"
if (-not (Test-Path $path)) {
  throw "Missing file: $path"
}

$lines = Get-Content $path

# PowerShell arrays are 0-based. Vercel line 195 = index 194.
$lines[194] = "          value={loading ? '-' : String(monitoredSystemsCount)}"

[System.IO.File]::WriteAllLines(
  (Resolve-Path $path),
  $lines,
  [System.Text.UTF8Encoding]::new($false)
)

Write-Host "Fixed exact line in: $path"


# 2) Fix threat monitoring detections placeholder
$path = "apps/web/app/threat-monitoring-panel.tsx"
if (-not (Test-Path $path)) {
  throw "Missing file: $path"
}

$lines = Get-Content $path

# Vercel line 369 = index 368.
$lines[368] = "          value={runtimeLoading || dataLoading ? '-' : String(detections.length)}"

[System.IO.File]::WriteAllLines(
  (Resolve-Path $path),
  $lines,
  [System.Text.UTF8Encoding]::new($false)
)

Write-Host "Fixed exact line in: $path"


# 3) Print verification snippets
Write-Host ""
Write-Host "Verify dashboard snippet:"
$lines = Get-Content "apps/web/app/dashboard-executive-summary.tsx"
$lines[190..198]

Write-Host ""
Write-Host "Verify threat monitoring snippet:"
$lines = Get-Content "apps/web/app/threat-monitoring-panel.tsx"
$lines[365..371]

Write-Host ""
Write-Host "Done. Now run:"
Write-Host "cd apps\web"
Write-Host "npm run build"
Write-Host "cd ..\.."
Write-Host "git add apps/web/app/dashboard-executive-summary.tsx apps/web/app/threat-monitoring-panel.tsx"
Write-Host "git commit -m `"Fix corrupted monitored metrics placeholders`""
Write-Host "git push origin HEAD:fix/complete-claude-md"
