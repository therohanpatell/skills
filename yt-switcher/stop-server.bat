@echo off
title YT Switcher Server Stopper
echo ===================================================
echo   YT Switcher - Stopping Server
echo ===================================================

powershell -NoProfile -ExecutionPolicy Bypass -Command "$conn = Get-NetTCPConnection -LocalPort 8300 -ErrorAction SilentlyContinue; if ($conn) { Stop-Process -Id $conn.OwningProcess -Force -ErrorAction SilentlyContinue; Write-Host 'Server process killed on port 8300.' } else { Write-Host 'No server running on port 8300.' }; Get-Process -Name 'node','mpv' -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -match 'yt-switcher' -or $_.CommandLine -match 'ytswitcher' } | Stop-Process -Force -ErrorAction SilentlyContinue;"

echo ===================================================
echo Server stopped.
echo ===================================================
timeout /t 3
