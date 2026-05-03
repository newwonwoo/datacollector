@echo off
REM Collector 원클릭 실행 (Windows). 더블클릭 또는 명령줄 실행.
title Collector — YouTube 지식 파이프라인
cd /d "%~dp0"

REM Prefer 'py' launcher (most installs), fall back to 'python'
where py >nul 2>nul
if %errorlevel% equ 0 (
    py -3 -m collector app %*
) else (
    python -m collector app %*
)

REM On non-zero exit (crash/missing deps) hold the window open so the user
REM can read the error. On Ctrl+C we still pause so the same window can
REM be reused without a fresh launch.
if errorlevel 1 (
    echo.
    echo [collector] 종료 코드 %errorlevel%. 메시지 확인 후 아무 키나 누르세요...
    pause >nul
)
