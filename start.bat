@echo off
chcp 65001 >nul
title TuboPlay
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo.
    echo   No se ha encontrado Python.  Instala Python 3.10+ desde https://www.python.org
    echo   y marca la casilla "Add Python to PATH" durante la instalacion.
    echo.
    pause
    exit /b 1
)

python -c "import customtkinter, yt_dlp, vlc, PIL, lz4, requests" >nul 2>nul
if errorlevel 1 (
    echo.
    echo   Faltan dependencias.  Instalando...
    echo.
    python -m pip install -r requirements.txt
    echo.
)

python main.py %*
if errorlevel 1 (
    echo.
    echo   TuboPlay se ha cerrado con errores.  Ejecuta "python main.py --selftest" para ver el motivo.
    pause
)
