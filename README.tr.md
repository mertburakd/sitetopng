# Domain Screenshot Crawler (Python + Playwright)

[English](README.md) | [Türkçe](README.tr.md)

Bu arac, verilen bir domain icindeki sayfalari gezer ve tam sayfa (`full_page=True`) PNG ekran goruntuleri alir.

## Ozellikler

- `headless=False` ile gorunur tarayici kullanir.
- URL kesif sirasi: once `sitemap.xml` (ve sitemap index), sonra sayfa ici linkler.
- Varsayilan olarak ayni domain icinde kalir (opsiyonel subdomain destegi var).
- Opsiyonel unique layout modu: sadece farkli HTML desenlerine sahip sayfalarin screenshot'unu alir.
- Calisma ciktilari:
  - `manifest.jsonl`
  - `summary.json`
- Ekran goruntuleri:
  - `screenshots/`
- Dosya adlari URL'den okunur bicimde uretilir:
  - `/tr/blog/2024te-surdurulebilirlik/` -> `tr-blog-2024te-surdurulebilirlik.png`
  - Ayni ad olusursa `-2`, `-3` gibi ekler.

## Gereksinimler

- Python 3.10+
- Tercihen yuklu Chrome veya Edge

## Kurulum

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

Opsiyonel:
- Varsayilan `--browser auto`, once sistemdeki Chrome/Edge'i dener.
- Sadece Playwright Chromium kullanmak istersen:

```powershell
# Windows
python -m playwright install chromium
```

```bash
# macOS / Linux
python3 -m playwright install chromium
```

## Kullanim

### Windows (PowerShell)

```powershell
python crawler.py --start-url https://example.com
```

Sadece unique sayfa tipleri (Windows):

```powershell
python crawler.py --start-url https://example.com --unique-layout-only
```

### macOS / Linux (bash)

```bash
python3 crawler.py --start-url https://example.com
```

Tum argumanlar (Windows):

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

Tum argumanlar (macOS / Linux):

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

## CLI Argumanlari

- `--start-url` (zorunlu): baslangic URL.
- `--output-dir` (opsiyonel): cikti klasoru.
  - Varsayilan: `./captures/<domain>/<timestamp>`.
- `--timeout-ms` (opsiyonel): sayfa acilis timeout degeri (ms). Varsayilan `30000`.
- `--max-pages` (opsiyonel): islenecek maksimum sayfa.
  - `0` limitsiz demektir.
- `--include-subdomains` (opsiyonel): subdomainleri de dahil eder.
- `--unique-layout-only` (opsiyonel): sadece unique HTML layout desenleri icin screenshot alir.
  - Ayni desenler `skipped_duplicate_layout` olarak isaretlenir.
  - Liste/kart satiri adedi farklari (ornegin `foreach` ile basilan kayit sayisi) mumkun oldugunca template farki sayilmaz.
- `--no-sitemap` (opsiyonel): sitemap kesfini kapatir.
- `--no-link-crawl` (opsiyonel): sayfa ici link kesfini kapatir.
- `--browser` (opsiyonel): `auto|chromium|chrome|edge`.
  - Varsayilan `auto`: Chrome -> Edge -> Playwright Chromium.
- `--executable-path` (opsiyonel): tarayici executable tam yolu.
  - Verilirse `--browser` yerine bu yol kullanilir.

## Tarayici Yolu Ornekleri (Opsiyonel)

- Windows Chrome: `C:\Program Files\Google\Chrome\Application\chrome.exe`
- Windows Edge: `C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe`
- macOS Chrome: `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`
- macOS Edge: `/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge`
- Linux Chrome: `/usr/bin/google-chrome`
- Linux Edge: `/usr/bin/microsoft-edge`

## Cikti Yapisi

Ornek:

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

`manifest.jsonl` URL bazli kayitlar icerir:
- URL
- durum (`success`, `error`, `skipped_non_html`, `skipped_duplicate_layout`)
- sure
- hata mesaji
- screenshot dosyasi
- layout signature (unique mod icin)
- duplicate oldugu referans URL

`summary.json` toplam kosu ozetini icerir:
- bulunan URL sayisi
- islenen URL sayisi
- basarili/hatali sayilar
- duplicate layout atlanan sayisi
- unique layout screenshot sayisi
- baslangic ve bitis zamanlari

## Notlar

- `robots.txt` okunur, `Disallow` kurallari sadece loglanir (engellenmez).
- Ozel auth/login akisi yoktur.
- Navigasyon bekleme stratejisi sabit `domcontentloaded` kullanir.
- Gorunur tarayici modu headless moda gore daha yavas olabilir.

## Lisans

MIT. Bkz: [LICENSE](LICENSE).
