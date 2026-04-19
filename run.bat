@echo off
REM Collector 원클릭 실행 (Windows). 더블클릭 또는 명령줄 실행.
cd /d "%~dp0"
where py >nul 2>nul && (py -3 -m collector app %*) || (python -m collector app %*)
