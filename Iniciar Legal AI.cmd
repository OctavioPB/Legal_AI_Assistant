@echo off
rem Doble clic: instala lo que falte (preguntando antes de descargar), prepara y abre Legal AI Assistant.
rem Todo corre en este equipo (127.0.0.1). Puede cerrar esta ventana cuando se abra el navegador.
title Legal AI Assistant
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\dev-up.ps1" %*
if errorlevel 1 (
  echo.
  echo No se pudo iniciar. Lea el mensaje de arriba; los registros estan en la carpeta .run
  pause
  exit /b 1
)
timeout /t 8 >nul 2>&1
exit /b 0
