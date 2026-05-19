# build.ps1 - Packages the app for a non-technical user (no Python needed on their PC)
# Double-click build.bat when YOU are ready to create the sendable zip.

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Set-Location $PSScriptRoot

$venvScripts = Join-Path $PSScriptRoot ".venv\Scripts"
$venvPython = Join-Path $venvScripts "python.exe"
$venvPip = Join-Path $venvScripts "pip.exe"
$playwrightDir = Join-Path $PSScriptRoot "ms-playwright"
$distDir = Join-Path $PSScriptRoot "dist\OWAScraper"
$zipPath = Join-Path $PSScriptRoot "outlookscraper.zip"

if (-not (Test-Path $venvPython)) {
    throw ".venv not found. Run once: python -m venv .venv"
}

$env:Path = "$venvScripts;$env:Path"
$env:VIRTUAL_ENV = Join-Path $PSScriptRoot ".venv"

function Wait-ForKey {
    Write-Host ""
    Write-Host "Press Enter to close..." -ForegroundColor DarkGray
    Read-Host | Out-Null
}

function Ensure-PlaywrightBrowsers {
    # Dev often uses %LOCALAPPDATA%\ms-playwright (why python app.py works without this folder)
    if (Test-Path $playwrightDir) {
        $hasChromium = Get-ChildItem $playwrightDir -Directory -Filter "chromium-*" -ErrorAction SilentlyContinue
        if ($hasChromium) {
            Write-Host "      Using existing ms-playwright in project" -ForegroundColor Green
            return
        }
    }

    Write-Host "      Downloading Chromium into project (one-time, ~180 MB)..." -ForegroundColor DarkGray
    $env:PLAYWRIGHT_BROWSERS_PATH = $playwrightDir
    & $venvPython -m playwright install chromium

    if (-not (Test-Path $playwrightDir)) {
        $appDataBrowsers = Join-Path $env:LOCALAPPDATA "ms-playwright"
        if (Test-Path $appDataBrowsers) {
            Write-Host "      Copying browsers from $appDataBrowsers ..." -ForegroundColor DarkGray
            Copy-Item -Path $appDataBrowsers -Destination $playwrightDir -Recurse -Force
        }
    }

    if (-not (Test-Path $playwrightDir)) {
        throw "Could not find or create ms-playwright. Run: .\.venv\Scripts\python.exe -m playwright install chromium"
    }
}

try {
    Write-Host ""
    Write-Host "=== OWA Scraper - Build for distribution ===" -ForegroundColor Cyan
    Write-Host ""

    Write-Host "[1/4] Python packages (venv)..." -ForegroundColor Yellow
    & $venvPip install -r requirements.txt -q
    Write-Host "      OK" -ForegroundColor Green

    Write-Host "[2/4] Chromium for bundling..." -ForegroundColor Yellow
    Ensure-PlaywrightBrowsers
    Write-Host "      OK: $playwrightDir" -ForegroundColor Green

    Write-Host "[3/4] App icon..." -ForegroundColor Yellow
    if (-not (Test-Path "icon.ico")) {
        & $venvPython make_icon.py 2>$null
    }
    Write-Host "      OK" -ForegroundColor Green

    Write-Host "[4/4] Creating OWAScraper.exe (PyInstaller)..." -ForegroundColor Yellow
    $iconArg = @()
    if (Test-Path "icon.ico") {
        $iconArg = @("--icon", "icon.ico")
    }

    & "$venvScripts\pyinstaller.exe" @iconArg `
        --noconsole `
        --onedir `
        --name OWAScraper `
        --add-data "templates;templates" `
        --add-data "static;static" `
        --add-data "icon.png;." `
        --add-data "ms-playwright;ms-playwright" `
        --collect-all playwright `
        --hidden-import=flask `
        --hidden-import=pystray `
        --hidden-import=PIL `
        --hidden-import=pandas `
        --hidden-import=openpyxl `
        --hidden-import=playwright `
        --hidden-import=owa_scraper `
        --hidden-import=dateutil.parser `
        --noconfirm `
        app.py

    if (-not (Test-Path "$distDir\OWAScraper.exe")) {
        throw "Build finished but OWAScraper.exe was not created."
    }

    @"
Outlook Scraper
===============

1. Double-click OWAScraper.exe
2. Your browser opens automatically - use the app there
3. First time only: if Windows warns, click More info then Run anyway
4. To sign in to Outlook, click Sign In in the app (a Chrome window opens)
5. Excel files are saved in the output folder next to this exe

Do NOT move only the .exe file - keep all files in this folder together.

To fully quit (closing the browser is NOT enough):
  - Click "Quit app" in the top-right of the app, OR
  - Right-click the tray icon (near the clock) and choose Quit
"@ | Set-Content -Path "$distDir\HOW TO USE.txt" -Encoding UTF8

    Write-Host ""
    Write-Host "=== BUILD COMPLETE ===" -ForegroundColor Green
    Write-Host ""
    Write-Host "Your app is ready:" -ForegroundColor Cyan
    Write-Host "  $distDir\OWAScraper.exe" -ForegroundColor White
    Write-Host ""

  # Zip is optional - close OWAScraper.exe first if it is running
  $zipOk = $false
  Get-Process -Name "OWAScraper" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
  Start-Sleep -Seconds 2

  for ($i = 1; $i -le 3; $i++) {
    try {
      if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
      $tempZip = "$zipPath.part"
      if (Test-Path $tempZip) { Remove-Item $tempZip -Force }
      Compress-Archive -Path $distDir -DestinationPath $tempZip -Force
      Move-Item -Path $tempZip -Destination $zipPath -Force
      $zipOk = $true
      break
    }
    catch {
      if ($i -lt 3) {
        Write-Host "Zip attempt $i failed (files may be locked). Retrying in 3s..." -ForegroundColor Yellow
        Start-Sleep -Seconds 3
      }
    }
  }

  if ($zipOk) {
    Write-Host "Zip ready to send:" -ForegroundColor Cyan
    Write-Host "  $zipPath" -ForegroundColor White
    Write-Host ""
    Write-Host "Your boss unzips it and double-clicks OWAScraper.exe." -ForegroundColor Gray
  }
  else {
    Write-Host "Could not create zip (a file was locked - often antivirus)." -ForegroundColor Yellow
    Write-Host "Manually zip this folder instead:" -ForegroundColor Yellow
    Write-Host "  $distDir" -ForegroundColor White
    Write-Host "Right-click the OWAScraper folder -> Send to -> Compressed (zipped) folder" -ForegroundColor Gray
  }
  Write-Host ""
}
catch {
    Write-Host ""
    Write-Host "BUILD FAILED: $($_.Exception.Message)" -ForegroundColor Red
    if ($_.ScriptStackTrace) {
        Write-Host $_.ScriptStackTrace -ForegroundColor DarkRed
    }
    $global:LASTEXITCODE = 1
}
finally {
    Wait-ForKey
}

if ($global:LASTEXITCODE) { exit $global:LASTEXITCODE }
