@echo off
setlocal
set "INPUT=%~1"
if "%INPUT%"=="" (
    echo [!] No file dragged onto the script.
    pause
    exit /b
)
set "OUTPUT=%~dpn1_safe%~x1"

:: Route to ComfyUI Portable embedded python
set "PYTHON_EXE=%~dp0..\..\python_embeded\python.exe"
if not exist "%PYTHON_EXE%" (
    :: Fallback if folder structure is different
    set "PYTHON_EXE=python"
)

"%PYTHON_EXE%" "%~dp0cipher.py" --mode forward --input "%INPUT%" --output "%OUTPUT%" --dict "%~dp0cipher_dict.json"
echo.
pause
