# Create a desktop shortcut for Collector (Windows).
# (ASCII-only to avoid PowerShell 5.1 codepage issues on Korean systems.)
#
# Run once from the repo root:
#   powershell -ExecutionPolicy Bypass -File scripts\install_desktop_shortcut.ps1
#
# Result: a 'Collector.lnk' on your Desktop. Double-click it to launch
# the local server; the browser opens http://127.0.0.1:8765 automatically.
# Survives 'git pull' as long as run.bat stays in the repo root.

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$RunBat   = Join-Path $RepoRoot "run.bat"

if (-not (Test-Path $RunBat)) {
    Write-Error "run.bat not found at: $RunBat"
    exit 1
}

$Desktop      = [Environment]::GetFolderPath("Desktop")
$ShortcutPath = Join-Path $Desktop "Collector.lnk"

$Shell    = New-Object -ComObject WScript.Shell
$Shortcut = $Shell.CreateShortcut($ShortcutPath)

$CmdExe       = Join-Path $env:WINDIR "System32\cmd.exe"
$PowerShellEx = Join-Path $env:WINDIR "System32\WindowsPowerShell\v1.0\powershell.exe"

$Shortcut.TargetPath       = $CmdExe
$Shortcut.Arguments        = "/c `"$RunBat`""
$Shortcut.WorkingDirectory = $RepoRoot
$Shortcut.WindowStyle      = 1
$Shortcut.Description      = "Collector - YouTube knowledge pipeline"
# PowerShell icon (blue), nicer than the default cmd icon.
$Shortcut.IconLocation     = "$PowerShellEx,0"
$Shortcut.Save()

Write-Host ""
Write-Host "  Desktop shortcut created." -ForegroundColor Green
Write-Host "  Path: $ShortcutPath"
Write-Host ""
Write-Host "  Double-click 'Collector' on your desktop to launch."
Write-Host "  The browser will open http://127.0.0.1:8765 automatically."
Write-Host ""
