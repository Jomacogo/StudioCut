@echo off
title Creador de Videos por Capitulos
cd /d "%~dp0"
chcp 65001 >nul

:: Check Python
where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] No se encontro Python.
    echo Instala Python 3.10 o superior desde https://www.python.org/downloads/
    echo IMPORTANTE: marca "Add Python to PATH" durante la instalacion.
    pause
    exit /b 1
)

:: Install PyQt6 if needed
python -c "import PyQt6" >nul 2>&1
if errorlevel 1 (
    echo Preparando dependencias, solo la primera vez...
    python -m pip install -r requirements.txt >nul 2>&1
    if errorlevel 1 (
        echo.
        echo [ERROR] Fallo la instalacion de dependencias.
        echo Revisa tu conexion a internet y el archivo requirements.txt.
        pause
        exit /b 1
    )
)

:: Check FFmpeg
where ffmpeg >nul 2>&1
if errorlevel 1 (
    echo [AVISO] FFmpeg no esta en el PATH.
    echo Es necesario para renderizar videos.
    echo Instalalo con:  winget install ffmpeg
    echo.
    echo La app continuara, pero los renders pueden fallar.
    echo.
)

:: Start the application
echo.
echo Iniciando Creador de Videos por Capitulos...
python main.py
exit /b 0