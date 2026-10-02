@echo off
setlocal
if "%~1"=="" (
    echo Usage: DECRYPT_FROM_AGENT.bat FILE
    exit /b 2
)
uv run --project "%~dp0.." --locked bamboozle --root "%~dp1." decode "%~nx1"
exit /b %ERRORLEVEL%
