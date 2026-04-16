# Domain Screenshot Crawler (Python + Playwright)

[English](README.md) | [Türkçe](README.tr.md)

This tool crawls pages within a given domain and captures full-page (`full_page=True`) PNG screenshots.

## Features

- Uses a visible browser with `headless=False`.
- URL discovery flow: `sitemap.xml` (and sitemap index) first, then in-page links.
- Stays inside the same domain by default (optional subdomain support).
- Optional unique layout mode: only keeps screenshots for pages with distinct HTML layout patterns.
- Produces run outputs:
  - `manifest.jsonl`
  - `summary.json`
- Stores screenshots in:
  - `screenshots/`
- Screenshot names are URL-based and readable:
  - `/tr/blog/2024te-surdurulebilirlik/` -> `tr-blog-2024te-surdurulebilirlik.png`
  - If a name already exists, it appends `-2`, `-3`, etc.

## Requirements

- Python 3.10+
- Preferably installed Chrome or Edge

## Setup

### Windows (PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

### macOS (zsh/bash)

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
```

### Linux (bash)

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
```

Optional:
- Default `--browser auto` tries installed Chrome/Edge first.
- If you want Playwright Chromium only:

```powershell
# Windows
python -m playwright install chromium
```

```bash
# macOS / Linux
python3 -m playwright install chromium
```

## Usage

### Windows (PowerShell)

```powershell
python crawler.py --start-url https://example.com
```

Only unique page layouts (Windows):

```powershell
python crawler.py --start-url https://example.com --unique-layout-only
```

### macOS / Linux (bash)

```bash
python3 crawler.py --start-url https://example.com
```

All arguments (Windows):

```powershell
python crawler.py `
  --start-url https://example.com `
  --output-dir .\captures\manual-run `
  --browser auto `
  --executable-path "C:\Program Files\Google\Chrome\Application\chrome.exe" `
  --timeout-ms 30000 `
  --max-pages 0 `
  --include-subdomains `
  --unique-layout-only `
  --no-sitemap `
  --no-link-crawl
```

All arguments (macOS / Linux):

```bash
python3 crawler.py \
  --start-url https://example.com \
  --output-dir ./captures/manual-run \
  --browser auto \
  --executable-path "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --timeout-ms 30000 \
  --max-pages 0 \
  --include-subdomains \
  --unique-layout-only \
  --no-sitemap \
  --no-link-crawl
```

## CLI Arguments

- `--start-url` (required): start URL.
- `--output-dir` (optional): output directory.
  - Default: `./captures/<domain>/<timestamp>`.
- `--timeout-ms` (optional): page navigation timeout in milliseconds. Default `30000`.
- `--max-pages` (optional): max pages to process.
  - `0` means unlimited.
- `--include-subdomains` (optional): include subdomains.
- `--unique-layout-only` (optional): capture screenshots only for unique HTML layout patterns.
  - Duplicate layouts are logged as `skipped_duplicate_layout`.
  - Repeated list/card row counts (for example `foreach` item count differences) are ignored as much as possible.
- `--no-sitemap` (optional): disable sitemap discovery.
- `--no-link-crawl` (optional): disable in-page link discovery.
- `--browser` (optional): `auto|chromium|chrome|edge`.
  - Default `auto`: Chrome -> Edge -> Playwright Chromium.
- `--executable-path` (optional): full path to browser executable.
  - If set, it takes precedence over `--browser`.

## Browser Path Examples (Optional)

- Windows Chrome: `C:\Program Files\Google\Chrome\Application\chrome.exe`
- Windows Edge: `C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe`
- macOS Chrome: `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`
- macOS Edge: `/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge`
- Linux Chrome: `/usr/bin/google-chrome`
- Linux Edge: `/usr/bin/microsoft-edge`

## Output Structure

Example:

```text
captures/
  example.com/
    20260402_153000/
      screenshots/
        home.png
        solutions-payroll.png
        tr-blog-2024te-surdurulebilirlik.png
      manifest.jsonl
      summary.json
```

`manifest.jsonl` contains per-URL records:
- URL
- status (`success`, `error`, `skipped_non_html`, `skipped_duplicate_layout`)
- duration
- error message
- screenshot file
- layout signature (for unique mode)
- duplicate-of URL (for duplicate layout rows)

`summary.json` contains run summary:
- total discovered URLs
- total processed URLs
- success/error counts
- skipped duplicate layout count
- unique layout screenshot count
- start and end times

## Notes

- `robots.txt` is read, and `Disallow` rules are only logged (not enforced).
- No special auth/login flow is implemented.
- Navigation wait strategy is fixed to `domcontentloaded`.
- Visible browser mode is usually slower than headless mode.

## License

MIT. See [LICENSE](LICENSE).
