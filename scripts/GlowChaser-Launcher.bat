@echo off
setlocal
title GlowChaser Launcher
set "PS1=%~dp0glowchaser.ps1"
if not exist "%PS1%" set "PS1=E:\Glowchaser\scripts\glowchaser.ps1"
if not exist "%PS1%" (
  echo Cannot find glowchaser.ps1
  pause
  exit /b 1
)
powershell.exe -NoProfile -NoLogo -ExecutionPolicy Bypass -File "%PS1%" %*
endlocal
