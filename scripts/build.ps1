# Builds the standalone exe + installer.
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\build.ps1
param([string]$Version = "")

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not $Version) {
    $Version = (Select-String -Path "src\osmooffload\__init__.py" -Pattern '__version__ = "([^"]+)"').Matches[0].Groups[1].Value
}
Write-Host "Building Osmo Offload v$Version"

& ".venv\Scripts\pyinstaller" --noconfirm --clean --onefile --windowed `
    --name OsmoOffload --icon assets\icon.ico --paths src gui_entry.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$iscc = @(
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles(x86)\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "Inno Setup not found (winget install -e --id JRSoftware.InnoSetup)" }

& $iscc "-DAppVersion=$Version" "installer\installer.iss"
if ($LASTEXITCODE -ne 0) { throw "ISCC failed" }

Write-Host "Done:"
Get-ChildItem dist\*.exe | ForEach-Object { Write-Host "  $($_.Name)  $([math]::Round($_.Length/1MB,1)) MB" }
