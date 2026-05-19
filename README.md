# Outlook Scraper

Desktop app that signs into Outlook Web, searches emails by sender, and exports results to Excel.

## Requirements

- Windows 10/11
- Python 3.11+ (for development only)

## Development setup

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\playwright install chromium
.\.venv\Scripts\python.exe app.py
```

Sign in once via the app UI. Session is saved as `owa_session.json` (gitignored).

## Build distributable for end users

```powershell
.\build.bat
```

Output:

- `dist\OWAScraper\OWAScraper.exe` — test locally
- `outlookscraper.zip` — send this zip to non-technical users

They unzip and double-click **OWAScraper.exe**. Use **Quit app** in the UI to fully exit (closing the browser is not enough).

## Project layout

| Path | Purpose |
|------|---------|
| `app.py` | Flask UI + system tray |
| `owa_scraper.py` | Playwright scraping logic |
| `templates/` / `static/` | Web UI |
| `build.ps1` / `build.bat` | PyInstaller packaging |
