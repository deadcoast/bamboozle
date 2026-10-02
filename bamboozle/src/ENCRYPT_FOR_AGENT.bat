@echo off
setlocal
if "%~1"=="" (
    echo Usage: ENCRYPT_FOR_AGENT.bat FILE
    exit /b 2
)
uv run --project "%~dp0.." --locked bamboozle --root "%~dp1." encode "%~nx1"
exit /b %ERRORLEVEL%
