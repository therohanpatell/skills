<#
.SYNOPSIS
  One-time Windows setup for yt-switcher.
  Checks/installs every external dependency: Node.js, Python, yt-dlp,
  FFmpeg, mpv, pyvirtualcam, and verifies the OBS Virtual Camera driver.

.NOTES
  Run from an elevated-or-normal PowerShell inside the yt-switcher folder:
    powershell -ExecutionPolicy Bypass -File scripts\setup-windows.ps1
  Uses winget where a tool is missing. Re-run any time; it is idempotent.
#>

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot

function Test-Command($name) {
    return [bool](Get-Command $name -ErrorAction SilentlyContinue)
}

function Install-IfMissing($cmd, $wingetId, $label) {
    if (Test-Command $cmd) {
        Write-Host "[ok] $label found: $((Get-Command $cmd).Source)" -ForegroundColor Green
        return
    }
    Write-Host "[..] $label not found - installing via winget ($wingetId)" -ForegroundColor Yellow
    winget install --id $wingetId -e --accept-source-agreements --accept-package-agreements
    if (-not (Test-Command $cmd)) {
        Write-Warning "$label was installed but is not on PATH yet. Open a NEW terminal and re-run this script."
        exit 1
    }
}

Write-Host "`n=== yt-switcher Windows setup ===`n"

# 1. Core runtimes
Install-IfMissing 'node'   'OpenJS.NodeJS.LTS' 'Node.js'
Install-IfMissing 'python' 'Python.Python.3.12' 'Python 3'

# 2. Media tools
Install-IfMissing 'yt-dlp' 'yt-dlp.yt-dlp' 'yt-dlp'
Install-IfMissing 'ffmpeg' 'Gyan.FFmpeg'   'FFmpeg'
Install-IfMissing 'mpv'    'mpv.net'        'mpv'   # mpv.io builds also fine: shinchiro builds

# 3. Node dependencies
Write-Host "[..] Installing Node dependencies"
Push-Location $root
npm install --no-audit --no-fund
Pop-Location
Write-Host "[ok] Node dependencies installed" -ForegroundColor Green

# 4. Python bridge dependencies
Write-Host "[..] Installing Python bridge dependencies (pyvirtualcam, numpy)"
python -m pip install --quiet --upgrade -r (Join-Path $root 'bridge\requirements.txt')
Write-Host "[ok] Python bridge dependencies installed" -ForegroundColor Green

# 5. OBS Virtual Camera driver check.
#    The driver registers this CLSID; present = vcam available without OBS running.
$obsVcamDll = @(
    "$env:ProgramFiles\obs-studio\data\obs-plugins\win-dshow\obs-virtualcam-module64.dll",
    "$env:ProgramFiles\obs-studio\bin\64bit\obs-virtualcam-module64.dll"
) | Where-Object { Test-Path $_ }

$clsid = Get-Item 'Registry::HKEY_CLASSES_ROOT\CLSID\{A3FCE0F5-3493-419F-958A-ABA1250EC20B}' -ErrorAction SilentlyContinue

if ($clsid -or $obsVcamDll) {
    Write-Host "[ok] OBS Virtual Camera driver detected" -ForegroundColor Green
} else {
    Write-Warning @"
OBS Virtual Camera driver NOT detected.
Install OBS Studio once (it ships the signed virtual camera driver):
    winget install --id OBSProject.OBSStudio -e
Then start OBS one time and click 'Start Virtual Camera' once (this registers
the device), stop it, and close OBS. OBS never needs to run again.
"@
}

# 5b. Branded virtual camera: "YT Switcher Virtual Cam" (akvirtualcamera).
#     Independent of OBS; works in vMix/OBS/Zoom/Teams/Google Meet.
$akMgr = @(
    "$env:ProgramFiles\AkVirtualCamera\x64\AkVCamManager.exe",
    "C:\Tools\AkVirtualCamera\x64\AkVCamManager.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $akMgr) {
    Write-Host "[..] akvirtualcamera not found - downloading latest release" -ForegroundColor Yellow
    try {
        $rel = Invoke-RestMethod 'https://api.github.com/repos/webcamoid/akvirtualcamera/releases/latest'
        $asset = $rel.assets | Where-Object { $_.name -match 'windows.*\.exe$' } | Select-Object -First 1
        if ($asset) {
            $dl = Join-Path $env:TEMP $asset.name
            Invoke-WebRequest $asset.browser_download_url -OutFile $dl
            Write-Host "[..] Running akvirtualcamera installer (accept the prompts)"
            Start-Process $dl -Wait
            $akMgr = "$env:ProgramFiles\AkVirtualCamera\x64\AkVCamManager.exe"
        }
    } catch {
        Write-Warning "Could not download akvirtualcamera automatically: $_"
        Write-Warning "Install manually from https://github.com/webcamoid/akvirtualcamera/releases - the app falls back to the OBS Virtual Camera meanwhile."
    }
}

if ($akMgr -and (Test-Path $akMgr)) {
    $existing = (& $akMgr devices) 2>$null | ForEach-Object {
        $id = $_.Trim()
        if ($id) { (& $akMgr description $id) 2>$null }
    }
    if ($existing -contains 'YT Switcher Virtual Cam') {
        Write-Host "[ok] 'YT Switcher Virtual Cam' already exists" -ForegroundColor Green
    } else {
        Write-Host "[..] Creating 'YT Switcher Virtual Cam' (may require Administrator)"
        try {
            $newId = (& $akMgr add-device 'YT Switcher Virtual Cam' | Select-Object -Last 1).Trim()
            if (-not $newId) { throw 'add-device returned no device id' }
            & $akMgr add-format $newId NV12 1920 1080 60 | Out-Null
            & $akMgr add-format $newId NV12 1280 720 30  | Out-Null
            & $akMgr add-format $newId RGB24 1280 720 30 | Out-Null
            & $akMgr update | Out-Null
            Write-Host "[ok] 'YT Switcher Virtual Cam' created ($newId)" -ForegroundColor Green
        } catch {
            Write-Warning "Could not create the branded camera: $_"
            Write-Warning "Re-run this script from an ADMINISTRATOR PowerShell to create it."
        }
    }
}

# 5c. Branded audio: virtual audio devices need a signed kernel driver, so we
#     use VB-Audio Cable and RENAME its endpoint to 'YT Switcher Audio'.
Write-Host ""
Write-Host "=== Branded audio device ('YT Switcher Audio') ==="
Write-Host @"
 1. Install VB-Audio Cable (free): https://vb-audio.com/Cable/  then reboot.
 2. Rename its endpoints (10 seconds, one time):
      Windows search 'mmsys.cpl' -> Playback tab -> 'CABLE Input'
        -> Properties -> rename the text box at the top to: YT Switcher Audio
      Recording tab -> 'CABLE Output' -> Properties -> rename to: YT Switcher Audio
 3. In the app side panel -> MONITOR -> Audio out -> pick 'YT Switcher Audio'.
 4. In Zoom/Teams/Meet/vMix pick microphone 'YT Switcher Audio'.
"@

# 6. Sanity check the bridge can open the camera (2-second probe).
Write-Host "[..] Probing virtual camera access"
$probe = @'
import sys
try:
    import pyvirtualcam
    with pyvirtualcam.Camera(width=1280, height=720, fps=30) as cam:
        print(f"[ok] virtual camera opened: {cam.device}")
except Exception as e:
    print(f"[!!] virtual camera probe failed: {e}", file=sys.stderr)
    sys.exit(1)
'@
$probe | python -
if ($LASTEXITCODE -ne 0) {
    Write-Warning "Virtual camera probe failed - see message above. The app will still run; fix the driver and press 'Reset bridge' in the UI."
}

Write-Host "`n=== Setup complete ==="
Write-Host "Start the app with:   npm start"
Write-Host "Then open:            http://127.0.0.1:8300`n"
