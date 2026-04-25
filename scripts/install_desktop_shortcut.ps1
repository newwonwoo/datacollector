# Collector 바탕화면 바로가기 만들기 (Windows).
#
# 사용:
#   PowerShell 우클릭 "관리자 권한으로 실행" 필요 없음. 일반 PS 창에서
#       cd C:\Users\<유저>\datacollector
#       powershell -ExecutionPolicy Bypass -File scripts\install_desktop_shortcut.ps1
#
# 결과:
#   바탕화면에 "Collector.lnk" 생성 → 더블클릭하면 run.bat 실행되며
#   브라우저가 자동으로 http://127.0.0.1:8765 를 엽니다.
#
# 한 번만 실행하면 끝. 코드 업데이트 후에도 바로가기는 그대로 동작합니다.

$ErrorActionPreference = "Stop"

# 이 스크립트의 부모 디렉터리 = 저장소 루트
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$RunBat   = Join-Path $RepoRoot "run.bat"

if (-not (Test-Path $RunBat)) {
    Write-Error "run.bat 을 찾을 수 없습니다: $RunBat"
    exit 1
}

$Desktop      = [Environment]::GetFolderPath("Desktop")
$ShortcutPath = Join-Path $Desktop "Collector.lnk"

$Shell    = New-Object -ComObject WScript.Shell
$Shortcut = $Shell.CreateShortcut($ShortcutPath)

# CMD 로 run.bat 을 실행. 콘솔창이 같이 떠서 진행 로그를 볼 수 있게 함.
$Shortcut.TargetPath       = "$env:WINDIR\System32\cmd.exe"
$Shortcut.Arguments        = "/c `"$RunBat`""
$Shortcut.WorkingDirectory = $RepoRoot
$Shortcut.WindowStyle      = 1  # 일반 창
$Shortcut.Description      = "Collector — YouTube 지식 파이프라인"
# Windows 기본 cmd 아이콘 대신 보기 좋은 거: PowerShell 의 파란 아이콘
$Shortcut.IconLocation     = "$env:WINDIR\System32\WindowsPowerShell\v1.0\powershell.exe,0"
$Shortcut.Save()

Write-Host ""
Write-Host "  Collector 바로가기 생성 완료" -ForegroundColor Green
Write-Host "  위치: $ShortcutPath"
Write-Host ""
Write-Host "  바탕화면에서 'Collector' 아이콘 더블클릭 → 잠시 후 브라우저 자동 오픈"
Write-Host ""
