@echo off
title YT Switcher Server Starter
echo ===================================================
echo   YT Switcher - Starting Server
echo ===================================================

:: Navigate to current directory
cd /d "%~dp0"

:: Copy the URL to clipboard using Node.js
node -e "const fs=require('fs');let h='127.0.0.1',p='8300';try{const c=JSON.parse(fs.readFileSync('config/default.json'));if(c.server){h=c.server.host||h;p=c.server.port||p;}}catch(e){}try{fs.readFileSync('.env','utf8').split(/\r?\n/).forEach(l=>{const pts=l.trim().split('=');if(pts.length>=2){const k=pts[0].trim(),v=pts.slice(1).join('=').trim().replace(/['\x22]/g,'');if(k==='YTSW_HOST')h=v;if(k==='YTSW_PORT')p=v;}});}catch(e){}const url='http://'+h+':'+p;require('child_process').execSync('powershell -NoProfile -Command Set-Clipboard -Value '+url);console.log('URL '+url+' copied to clipboard!');"

echo.
echo Starting the server...
npm start

pause
