# Run from repo root: F:\Blockchain_Security3\decoda-rwa-guard
# Purpose: fix all corrupted metric loading placeholders in:
#   apps/web/app/dashboard-executive-summary.tsx
#   apps/web/app/threat-monitoring-panel.tsx
#
# It fixes lines like:
#   value={loading ? '闁? : String(activeAlertsCount)}
#   value={runtimeLoading || dataLoading ? '闁? : String(anomalies.length)}
#
# into:
#   value={loading ? '-' : String(activeAlertsCount)}
#   value={runtimeLoading || dataLoading ? '-' : String(anomalies.length)}

$ErrorActionPreference = "Stop"

$files = @(
  "apps/web/app/dashboard-executive-summary.tsx",
  "apps/web/app/threat-monitoring-panel.tsx"
)

foreach ($file in $files) {
  if (-not (Test-Path $file)) {
    throw "Missing file: $file"
  }

  $lines = Get-Content $file

  for ($i = 0; $i -lt $lines.Count; $i++) {
    if ($lines[$i] -match "^\s*value=\{(.+?) \? '.+ : String\((.+)\)\}") {
      $indent = ($lines[$i] -replace "^(\s*).*$", '$1')
      $condition = $matches[1]
      $valueExpr = $matches[2]
      $lines[$i] = "$indent" + "value={$condition ? '-' : String($valueExpr)}"
    }
  }

  [System.IO.File]::WriteAllLines(
    (Resolve-Path $file),
    $lines,
    [System.Text.UTF8Encoding]::new($false)
  )

  Write-Host "Fixed placeholders in $file"
}

Write-Host ""
Write-Host "Dashboard verification snippet:"
$lines = Get-Content "apps/web/app/dashboard-executive-summary.tsx"
$lines[180..210]

Write-Host ""
Write-Host "Threat monitoring verification snippet:"
$lines = Get-Content "apps/web/app/threat-monitoring-panel.tsx"
$lines[360..380]

Write-Host ""
Write-Host "Done. Now run:"
Write-Host "cd apps\web"
Write-Host "npx next build"
Write-Host "cd ..\.."
Write-Host "git add apps/web/app/dashboard-executive-summary.tsx apps/web/app/threat-monitoring-panel.tsx"
Write-Host "git commit -m `"Fix corrupted metric loading placeholders`""
Write-Host "git push origin HEAD:fix/complete-claude-md"
