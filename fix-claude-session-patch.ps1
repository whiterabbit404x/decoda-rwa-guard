# Fix and apply a Claude-generated git patch.
# Run this from the repo root:
#   powershell -ExecutionPolicy Bypass -File fix-claude-session-patch.ps1

$ErrorActionPreference = "Stop"

$inputPatch = "claude-session.patch"
$outputPatch = "claude-session.fixed.patch"

if (!(Test-Path $inputPatch)) {
    Write-Host "ERROR: $inputPatch not found in current folder."
    Write-Host "Please run this script from your repo root: F:\Blockchain_Security3\decoda-rwa-guard"
    exit 1
}

Write-Host "Normalizing patch line endings and final newline..."
$patch = Get-Content -Raw $inputPatch
$patch = $patch -replace "`r`n", "`n"
$patch = $patch.TrimEnd() + "`n"
Set-Content -NoNewline -Encoding utf8 $outputPatch -Value $patch

Write-Host "Checking patch..."
git apply --check --whitespace=fix $outputPatch

Write-Host "Applying patch..."
git apply --whitespace=fix $outputPatch

Write-Host ""
Write-Host "Patch applied successfully."
Write-Host ""
Write-Host "Git status:"
git status

Write-Host ""
Write-Host "Diff stat:"
git diff --stat
