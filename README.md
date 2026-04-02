# Domain Screenshot Crawler (Python + Playwright)

Bu uygulama verilen bir domain icindeki sayfalari kesfeder ve her sayfadan tam boy (`full_page=True`) PNG ekran goruntusu alir.

## Ozellikler

- `headless=False` ile gorunur tarayici kullanir.
- Once `sitemap.xml` (ve sitemap index), sonra sayfa ici linklerle URL kesfi yapar.
- Sadece ayni domain URL'lerini gezer (opsiyonel olarak subdomain dahil edilebilir).
- Her URL icin sonuc kaydi olusturur:
  - `manifest.jsonl`
  - `summary.json`
- Ciktilari tek klasorde toplar:
  - `screenshots/`

## Gereksinimler

- Python 3.10+
- Tercihen sistemde kurulu Chrome veya Edge

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
- Varsayilan `--browser auto` oldugu icin once sistemdeki Chrome/Edge denenir.
- Eger sadece Playwright Chromium kullanmak istersen:

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
  --no-sitemap \
  --no-link-crawl
```

## CLI Parametreleri

- `--start-url` (zorunlu): Baslangic URL.
- `--output-dir` (opsiyonel): Cikti klasoru.
  - Verilmezse `./captures/<domain>/<timestamp>` kullanilir.
- `--timeout-ms` (opsiyonel): Sayfa acilis timeout degeri. Varsayilan `30000`.
- `--max-pages` (opsiyonel): Maksimum sayfa sayisi.
  - `0` => limitsiz.
- `--include-subdomains` (opsiyonel): Subdomainleri de dahil eder.
- `--no-sitemap` (opsiyonel): Sitemap kesfini kapatir.
- `--no-link-crawl` (opsiyonel): Sayfa ici link kesfini kapatir.
- `--browser` (opsiyonel): `auto|chromium|chrome|edge`.
  - Varsayilan `auto`: Chrome -> Edge -> Playwright Chromium sirasiyla dener.
- `--executable-path` (opsiyonel): Tarayici executable tam yolu.
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
        blog-how-to-manage-shifts__q-page-lang.png
      manifest.jsonl
      summary.json
```

Dosya adlari URL'e gore uretilir:
- Path segmentleri okunur bir slug haline gelir (`/blog/how-to-manage-shifts` -> `blog-how-to-manage-shifts`)
- Query tarafinda takip parametreleri (`utm_*`, `gclid`, `fbclid` vb.) ayiklanir
- Anlamli query key'leri ada eklenir (`__q-page-lang` gibi)
- Ayni ad olusursa dosya ezilmez, otomatik `-2`, `-3` eki verilir

`manifest.jsonl` satir bazli kayit icerir:
- URL
- durum (`success`, `error`, `skipped_non_html`)
- sure
- hata mesaji
- olusan screenshot dosyasi

`summary.json` toplu sonuc bilgilerini icerir:
- bulunan URL sayisi
- islenen URL sayisi
- basarili/hatali sayfa sayisi
- baslangic ve bitis zamanlari

## Notlar

- `robots.txt` okunur, `Disallow` kurallari sadece loglanir, engelleme uygulanmaz.
- Login gerektiren sayfalar icin ozel auth akisi yoktur.
- Navigasyon bekleme stratejisi sabit olarak `domcontentloaded` kullanir.
- Gorunur tarayici modunda oldugu icin tarama headless moda gore daha yavas olabilir.
