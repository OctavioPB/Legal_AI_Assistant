@echo off
rem Doble clic: cierra Legal AI Assistant (API y web) y libera sus puertos. Los datos se conservan.
title Legal AI Assistant
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\dev-up.ps1" -Stop %*
timeout /t 8 >nul 2>&1
exit /b 0
