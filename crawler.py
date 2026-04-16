#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen

USER_AGENT = "DomainScreenshotCrawler/1.0"
NAVIGATION_WAIT_UNTIL = "domcontentloaded"
TRACKING_QUERY_KEYS = {
    "fbclid",
    "gclid",
    "igshid",
    "mc_cid",
    "mc_eid",
    "msclkid",
    "ref",
    "source",
    "utm_campaign",
    "utm_content",
    "utm_id",
    "utm_medium",
    "utm_source",
    "utm_term",
}
SKIP_EXTENSIONS = {
    ".7z",
    ".avi",
    ".bmp",
    ".css",
    ".csv",
    ".doc",
    ".docx",
    ".eot",
    ".gif",
    ".gz",
    ".ico",
    ".jpeg",
    ".jpg",
    ".js",
    ".json",
    ".map",
    ".mov",
    ".mp3",
    ".mp4",
    ".mpeg",
    ".mpg",
    ".ogg",
    ".pdf",
    ".png",
    ".ppt",
    ".pptx",
    ".rar",
    ".rss",
    ".svg",
    ".tar",
    ".tgz",
    ".tif",
    ".tiff",
    ".ttf",
    ".txt",
    ".wav",
    ".webm",
    ".webp",
    ".woff",
    ".woff2",
    ".xls",
    ".xlsx",
    ".xml",
    ".zip",
}


@dataclass
class CrawlConfig:
    start_url: str
    output_dir: Path | None
    timeout_ms: int
    max_pages: int
    include_subdomains: bool
    sitemap_enabled: bool
    link_crawl_enabled: bool
    unique_layout_only: bool
    browser: str
    executable_path: Path | None


def log(message: str) -> None:
    stamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{stamp}] {message}", flush=True)


def to_iso_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def has_scheme(value: str) -> bool:
    return bool(re.match(r"^[A-Za-z][A-Za-z0-9+.\-]*://", value))


def canonical_netloc(scheme: str, split_result) -> str:
    host = (split_result.hostname or "").lower()
    if not host:
        return ""
    port = split_result.port
    if port is None:
        return host
    if (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
        return host
    return f"{host}:{port}"


def normalize_url(url: str) -> str | None:
    raw = (url or "").strip()
    if not raw:
        return None

    split_result = urlsplit(raw)
    scheme = split_result.scheme.lower()
    if scheme not in {"http", "https"}:
        return None

    netloc = canonical_netloc(scheme, split_result)
    if not netloc:
        return None

    path = split_result.path or "/"
    path = re.sub(r"/{2,}", "/", path)
    if path != "/" and path.endswith("/"):
        path = path[:-1]

    query_pairs = parse_qsl(split_result.query, keep_blank_values=True)
    query = urlencode(sorted(query_pairs))
    return urlunsplit((scheme, netloc, path, query, ""))


def normalize_start_url(start_url: str) -> str:
    candidate = start_url.strip()
    if not candidate:
        raise ValueError("--start-url bos olamaz.")
    if not has_scheme(candidate):
        candidate = f"https://{candidate}"

    normalized = normalize_url(candidate)
    if not normalized:
        raise ValueError("Gecersiz --start-url verildi.")
    return normalized


def domain_allowed(url: str, base_host: str, include_subdomains: bool) -> bool:
    hostname = (urlsplit(url).hostname or "").lower()
    if not hostname:
        return False
    if hostname == base_host:
        return True
    return include_subdomains and hostname.endswith(f".{base_host}")


def is_probably_html(url: str) -> bool:
    path = urlsplit(url).path
    suffix = Path(path).suffix.lower()
    if not suffix:
        return True
    return suffix not in SKIP_EXTENSIONS


def filesystem_safe(value: str, fallback: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-._")
    return text[:80] if text else fallback


def slugify_url(url: str) -> str:
    split_result = urlsplit(url)
    raw_parts = [unquote(p) for p in split_result.path.split("/") if p]
    path_parts: list[str] = []

    for raw in raw_parts:
        safe = filesystem_safe(raw.lower(), fallback="part")
        if re.fullmatch(r"\d+", safe):
            path_parts.append("id")
        elif re.fullmatch(r"[a-f0-9]{8,}", safe):
            path_parts.append("hexid")
        else:
            path_parts.append(safe)

    if not path_parts:
        path_parts = ["home"]

    base = "-".join(path_parts)

    query_pairs = parse_qsl(split_result.query, keep_blank_values=True)
    query_keys = sorted(
        {
            filesystem_safe(key.lower(), fallback="q")
            for key, _ in query_pairs
            if key and key.lower() not in TRACKING_QUERY_KEYS
        }
    )
    if query_keys:
        query_suffix = "-".join(query_keys[:5])
        base = f"{base}__q-{query_suffix}"

    if len(base) > 110:
        base = base[:110].rstrip("-_.")
    return filesystem_safe(base, fallback="page")


def build_unique_screenshot_name(
    slug: str,
    screenshots_dir: Path,
    used_names: set[str],
) -> str:
    base = filesystem_safe(slug, fallback="page")
    candidate = f"{base}.png"
    counter = 2

    while candidate in used_names or (screenshots_dir / candidate).exists():
        candidate = f"{base}-{counter}.png"
        counter += 1

    used_names.add(candidate)
    return candidate


def normalize_layout_token(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9_-]+", "-", value.lower()).strip("-_")
    return cleaned[:24]


def is_stable_class_token(token: str) -> bool:
    if not token:
        return False
    digit_count = sum(character.isdigit() for character in token)
    if digit_count >= 4:
        return False
    if len(token) >= 24 and digit_count > 0:
        return False
    return True


def build_layout_signature(html: str) -> str:
    from bs4 import BeautifulSoup, Comment, NavigableString, Tag

    soup = BeautifulSoup(html, "lxml")

    for selector in ("script", "style", "noscript", "template", "svg"):
        for element in soup.find_all(selector):
            element.decompose()

    for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
        comment.extract()

    root = soup.body or soup
    max_nodes = 2500
    stack: list[tuple[object, int]] = [(root, 0)]
    tokens: list[str] = []
    tag_counts: dict[str, int] = {}

    while stack and len(tokens) < max_nodes:
        node, depth = stack.pop()
        if not isinstance(node, Tag):
            continue

        node_name = (node.name or "").lower()
        child_nodes = list(node.children)
        child_tags = [child for child in child_nodes if isinstance(child, Tag)]

        for child in reversed(child_tags):
            stack.append((child, depth + 1))

        if node_name in {"[document]", "html", "body"}:
            continue

        tag_counts[node_name] = tag_counts.get(node_name, 0) + 1
        has_text = any(
            isinstance(child, NavigableString) and str(child).strip()
            for child in child_nodes
        )

        token_parts = [
            node_name,
            f"d{min(depth, 8)}",
            f"k{min(len(child_tags), 9)}",
            f"t{1 if has_text else 0}",
        ]

        role_value = node.get("role")
        if isinstance(role_value, str):
            role_token = normalize_layout_token(role_value)
            if role_token:
                token_parts.append(f"r:{role_token}")

        type_value = node.get("type")
        if isinstance(type_value, str):
            type_token = normalize_layout_token(type_value)
            if type_token:
                token_parts.append(f"y:{type_token}")

        class_values = node.get("class") or []
        stable_classes: list[str] = []
        for class_value in class_values:
            normalized_class = normalize_layout_token(str(class_value))
            if not is_stable_class_token(normalized_class):
                continue
            stable_classes.append(normalized_class)
            if len(stable_classes) >= 2:
                break
        if stable_classes:
            deduped = sorted(set(stable_classes))
            token_parts.append(f"c:{','.join(deduped)}")

        tokens.append("|".join(token_parts))

    top_counts = sorted(tag_counts.items(), key=lambda item: (-item[1], item[0]))[:16]
    payload = (
        f"nodes={len(tokens)}\n"
        + "\n".join(tokens)
        + "\n--\n"
        + "|".join(f"{name}:{count}" for name, count in top_counts)
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def fetch_text(url: str, timeout_seconds: float) -> tuple[int | None, str, str]:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    request = Request(url, headers=headers)

    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            body = response.read()
            content_type = response.headers.get("Content-Type", "")
            encoding = response.headers.get_content_charset() or "utf-8"
            text = body.decode(encoding, errors="replace")
            return response.getcode(), content_type, text
    except HTTPError as exc:
        content_type = exc.headers.get("Content-Type", "") if exc.headers else ""
        body = exc.read().decode("utf-8", errors="replace")
        return exc.code, content_type, body
    except URLError as exc:
        raise RuntimeError(f"URL okunamadi: {url} -> {exc}") from exc


def read_robots_data(base_origin: str, timeout_seconds: float) -> tuple[list[str], list[str]]:
    robots_url = f"{base_origin}/robots.txt"
    sitemap_urls: list[str] = []
    disallow_rules: list[str] = []

    try:
        _, _, text = fetch_text(robots_url, timeout_seconds=timeout_seconds)
    except Exception as exc:
        log(f"robots.txt okunamadi ({robots_url}): {exc}")
        return sitemap_urls, disallow_rules

    for line in text.splitlines():
        cleaned = line.strip()
        if not cleaned or cleaned.startswith("#"):
            continue
        lowered = cleaned.lower()
        if lowered.startswith("sitemap:"):
            value = cleaned.split(":", 1)[1].strip()
            normalized = normalize_url(value)
            if normalized:
                sitemap_urls.append(normalized)
        elif lowered.startswith("disallow:"):
            value = cleaned.split(":", 1)[1].strip()
            if value:
                disallow_rules.append(value)

    return sitemap_urls, disallow_rules


def discover_sitemap_urls(
    base_origin: str,
    base_host: str,
    include_subdomains: bool,
    timeout_seconds: float,
) -> set[str]:
    from bs4 import BeautifulSoup

    robots_sitemaps, disallow_rules = read_robots_data(base_origin, timeout_seconds)
    if disallow_rules:
        log(
            f"robots.txt icinde {len(disallow_rules)} Disallow kurali bulundu "
            "(engelleme uygulanmiyor, sadece bilgilendirme)."
        )

    initial_sitemaps = list(dict.fromkeys(robots_sitemaps + [f"{base_origin}/sitemap.xml"]))
    pending = deque(initial_sitemaps)
    seen_sitemaps: set[str] = set()
    discovered_urls: set[str] = set()

    while pending:
        sitemap_url = pending.popleft()
        normalized_sitemap = normalize_url(sitemap_url)
        if not normalized_sitemap or normalized_sitemap in seen_sitemaps:
            continue
        if not domain_allowed(normalized_sitemap, base_host, include_subdomains):
            continue

        seen_sitemaps.add(normalized_sitemap)
        log(f"Sitemap okunuyor: {normalized_sitemap}")

        try:
            _, _, text = fetch_text(normalized_sitemap, timeout_seconds=timeout_seconds)
        except Exception as exc:
            log(f"Sitemap atlandi ({normalized_sitemap}): {exc}")
            continue

        soup = BeautifulSoup(text, "xml")
        has_xml_structure = bool(soup.find("urlset") or soup.find("sitemapindex"))
        if has_xml_structure:
            for loc_tag in soup.find_all("loc"):
                loc_text = (loc_tag.text or "").strip()
                normalized_loc = normalize_url(loc_text)
                if not normalized_loc:
                    continue
                if not domain_allowed(normalized_loc, base_host, include_subdomains):
                    continue

                if normalized_loc.endswith(".xml"):
                    if normalized_loc not in seen_sitemaps:
                        pending.append(normalized_loc)
                    continue

                if is_probably_html(normalized_loc):
                    discovered_urls.add(normalized_loc)
            continue

        for raw in re.findall(r"https?://[^\s<>\"]+", text):
            normalized = normalize_url(raw)
            if not normalized:
                continue
            if not domain_allowed(normalized, base_host, include_subdomains):
                continue
            if is_probably_html(normalized):
                discovered_urls.add(normalized)

    log(
        f"Sitemap kesfi tamamlandi. {len(seen_sitemaps)} sitemap tarandi, "
        f"{len(discovered_urls)} URL bulundu."
    )
    return discovered_urls


def extract_internal_links(
    html: str,
    current_url: str,
    base_host: str,
    include_subdomains: bool,
) -> set[str]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    links: set[str] = set()

    for anchor in soup.find_all("a", href=True):
        href = (anchor.get("href") or "").strip()
        if not href:
            continue
        lowered = href.lower()
        if lowered.startswith(("mailto:", "tel:", "javascript:", "data:", "#")):
            continue

        absolute = urljoin(current_url, href)
        normalized = normalize_url(absolute)
        if not normalized:
            continue
        if not domain_allowed(normalized, base_host, include_subdomains):
            continue
        if not is_probably_html(normalized):
            continue
        links.add(normalized)

    return links


def auto_scroll_for_lazy_content(page) -> None:
    page.evaluate(
        """
        async () => {
            const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
            let previousHeight = -1;
            for (let i = 0; i < 40; i += 1) {
                window.scrollTo(0, document.body.scrollHeight);
                await sleep(250);
                const currentHeight = document.body.scrollHeight;
                if (currentHeight === previousHeight) break;
                previousHeight = currentHeight;
            }
            window.scrollTo(0, 0);
            await sleep(200);
        }
        """
    )


def build_output_dir(start_url: str, user_output_dir: Path | None) -> Path:
    if user_output_dir is not None:
        return user_output_dir.resolve()

    split_result = urlsplit(start_url)
    host = filesystem_safe(split_result.hostname or split_result.netloc, fallback="site")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return (Path.cwd() / "captures" / host / timestamp).resolve()


def launch_visible_browser(playwright, config: CrawlConfig):
    from playwright.sync_api import Error as PlaywrightError

    if config.executable_path is not None:
        browser_path = config.executable_path.expanduser().resolve()
        if not browser_path.exists():
            raise RuntimeError(f"--executable-path bulunamadi: {browser_path}")
        log(f"Tarayici aciliyor (executable-path): {browser_path}")
        return playwright.chromium.launch(
            headless=False,
            executable_path=str(browser_path),
        )

    browser_mode = config.browser.lower()
    launch_plan: list[tuple[str, dict]] = []
    if browser_mode == "auto":
        launch_plan = [
            ("chrome", {"channel": "chrome"}),
            ("edge", {"channel": "msedge"}),
            ("chromium", {}),
        ]
    elif browser_mode == "chrome":
        launch_plan = [("chrome", {"channel": "chrome"})]
    elif browser_mode == "edge":
        launch_plan = [("edge", {"channel": "msedge"})]
    else:
        launch_plan = [("chromium", {})]

    errors: list[str] = []
    for label, extra in launch_plan:
        try:
            log(f"Tarayici aciliyor: {label} (headless=False)")
            return playwright.chromium.launch(headless=False, **extra)
        except PlaywrightError as exc:
            errors.append(f"{label}: {exc}")

    error_text = " | ".join(errors) if errors else "Bilinmeyen hata"
    raise RuntimeError(
        "Tarayici baslatilamadi. "
        "Cozum: --browser chrome veya --browser edge deneyin, "
        "gerekirse --executable-path ile tarayici yolunu verin. "
        f"Ayrinti: {error_text}"
    )


def crawl(config: CrawlConfig) -> int:
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright bagimliligi bulunamadi. "
            "Lutfen `pip install -r requirements.txt` ve "
            "`python -m playwright install chromium` komutlarini calistirin."
        ) from exc

    start_url = normalize_start_url(config.start_url)
    split_start = urlsplit(start_url)
    base_host = (split_start.hostname or "").lower()
    base_origin = f"{split_start.scheme}://{split_start.netloc}"
    timeout_seconds = max(1.0, config.timeout_ms / 1000)

    output_dir = build_output_dir(start_url, config.output_dir)
    screenshots_dir = output_dir / "screenshots"
    screenshots_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = output_dir / "manifest.jsonl"
    summary_path = output_dir / "summary.json"

    log(f"Baslangic URL: {start_url}")
    log(f"Cikti klasoru: {output_dir}")
    log(f"Tarayici modu: gorunur (headless=False), secim={config.browser}")
    log(f"Navigasyon bekleme stratejisi: {NAVIGATION_WAIT_UNTIL}")
    if config.unique_layout_only:
        log("Unique layout modu aktif: sadece farkli HTML desenleri screenshot alinacak.")

    queue: deque[str] = deque()
    enqueued: set[str] = set()
    processed: set[str] = set()

    def enqueue(url: str, force: bool = False) -> bool:
        normalized = normalize_url(url)
        if not normalized:
            return False
        if normalized in enqueued:
            return False
        if not domain_allowed(normalized, base_host, config.include_subdomains):
            return False
        if not force and not is_probably_html(normalized):
            return False
        enqueued.add(normalized)
        queue.append(normalized)
        return True

    enqueue(start_url, force=True)

    if config.sitemap_enabled:
        sitemap_urls = discover_sitemap_urls(
            base_origin=base_origin,
            base_host=base_host,
            include_subdomains=config.include_subdomains,
            timeout_seconds=timeout_seconds,
        )
        newly_enqueued = 0
        for sitemap_url in sorted(sitemap_urls):
            if enqueue(sitemap_url):
                newly_enqueued += 1
        log(f"Sitemap kaynakli kuyruga eklenen URL sayisi: {newly_enqueued}")

    started_at = to_iso_now()
    started_perf = time.perf_counter()
    success_count = 0
    error_count = 0
    skipped_non_html_count = 0
    skipped_duplicate_layout_count = 0
    unique_layout_count = 0

    with manifest_path.open("a", encoding="utf-8") as manifest_file:
        with sync_playwright() as playwright:
            browser = launch_visible_browser(playwright, config)
            context = browser.new_context(ignore_https_errors=True)
            page = context.new_page()
            used_screenshot_names: set[str] = set()
            layout_signature_to_url: dict[str, str] = {}

            try:
                while queue:
                    if config.max_pages > 0 and len(processed) >= config.max_pages:
                        log(f"--max-pages siniri nedeniyle durduruldu: {config.max_pages}")
                        break

                    current_url = queue.popleft()
                    if current_url in processed:
                        continue
                    processed.add(current_url)

                    page_number = len(processed)
                    log(f"[{page_number}] Aciliyor: {current_url}")
                    page_started = time.perf_counter()
                    status = "success"
                    error_message = ""
                    screenshot_name = ""
                    http_status = None
                    content_type = ""
                    layout_signature = ""
                    duplicate_of_url = ""
                    discovered_links_count = 0
                    enqueued_links_count = 0
                    queue_after_pop = len(queue)

                    try:
                        response = page.goto(
                            current_url,
                            wait_until=NAVIGATION_WAIT_UNTIL,
                            timeout=config.timeout_ms,
                        )
                        if response is not None:
                            http_status = response.status
                            content_type = response.headers.get("content-type", "")

                        if content_type and (
                            "text/html" not in content_type
                            and "application/xhtml+xml" not in content_type
                        ):
                            status = "skipped_non_html"
                            skipped_non_html_count += 1
                            log(
                                f"[{page_number}] HTML degil ({content_type}), screenshot alinmadi."
                            )
                        else:
                            auto_scroll_for_lazy_content(page)
                            html = ""
                            if config.link_crawl_enabled or config.unique_layout_only:
                                html = page.content()

                            take_screenshot = True
                            if config.unique_layout_only:
                                layout_signature = build_layout_signature(html)
                                duplicate_of_url = layout_signature_to_url.get(layout_signature, "")
                                if duplicate_of_url:
                                    take_screenshot = False
                                    status = "skipped_duplicate_layout"
                                    skipped_duplicate_layout_count += 1
                                    log(
                                        f"[{page_number}] Layout duplicate, screenshot atlandi. "
                                        f"Ilk ornek: {duplicate_of_url}"
                                    )
                                else:
                                    layout_signature_to_url[layout_signature] = current_url
                                    unique_layout_count += 1

                            if take_screenshot:
                                screenshot_name = build_unique_screenshot_name(
                                    slug=slugify_url(current_url),
                                    screenshots_dir=screenshots_dir,
                                    used_names=used_screenshot_names,
                                )
                                screenshot_path = screenshots_dir / screenshot_name
                                page.screenshot(path=str(screenshot_path), full_page=True)
                                success_count += 1

                            if config.link_crawl_enabled:
                                links = extract_internal_links(
                                    html=html,
                                    current_url=current_url,
                                    base_host=base_host,
                                    include_subdomains=config.include_subdomains,
                                )
                                for link in links:
                                    if enqueue(link):
                                        enqueued_links_count += 1
                                discovered_links_count = len(links)
                    except PlaywrightTimeoutError:
                        status = "error"
                        error_message = f"Timeout ({config.timeout_ms} ms)"
                        error_count += 1
                        log(f"[{page_number}] Zaman asimi: {current_url}")
                    except Exception as exc:
                        status = "error"
                        error_message = str(exc)
                        error_count += 1
                        log(f"[{page_number}] Hata: {current_url} -> {exc}")

                    elapsed_ms = int((time.perf_counter() - page_started) * 1000)
                    manifest_record = {
                        "index": page_number,
                        "url": current_url,
                        "status": status,
                        "http_status": http_status,
                        "content_type": content_type,
                        "screenshot_file": screenshot_name,
                        "layout_signature": layout_signature,
                        "duplicate_of_url": duplicate_of_url,
                        "duration_ms": elapsed_ms,
                        "error": error_message,
                        "discovered_links": discovered_links_count,
                        "enqueued_links": enqueued_links_count,
                        "timestamp": to_iso_now(),
                    }
                    manifest_file.write(json.dumps(manifest_record, ensure_ascii=False) + "\n")
                    manifest_file.flush()

                    if status == "success":
                        log(
                            f"[{page_number}] Tamamlandi ({elapsed_ms} ms). "
                            f"Bulunan link: {discovered_links_count}, "
                            f"kuyruga eklenen: {enqueued_links_count}, "
                            f"kuyruk: {queue_after_pop} -> {len(queue)}"
                        )
            finally:
                context.close()
                browser.close()

    finished_at = to_iso_now()
    duration_seconds = round(time.perf_counter() - started_perf, 3)
    summary = {
        "start_url": start_url,
        "base_host": base_host,
        "include_subdomains": config.include_subdomains,
        "sitemap_enabled": config.sitemap_enabled,
        "link_crawl_enabled": config.link_crawl_enabled,
        "unique_layout_only": config.unique_layout_only,
        "browser": config.browser,
        "executable_path": str(config.executable_path) if config.executable_path else None,
        "wait_until": NAVIGATION_WAIT_UNTIL,
        "timeout_ms": config.timeout_ms,
        "max_pages": config.max_pages,
        "output_dir": str(output_dir),
        "screenshots_dir": str(screenshots_dir),
        "manifest_file": str(manifest_path),
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_seconds": duration_seconds,
        "discovered_total": len(enqueued),
        "processed_total": len(processed),
        "success_total": success_count,
        "error_total": error_count,
        "skipped_non_html_total": skipped_non_html_count,
        "skipped_duplicate_layout_total": skipped_duplicate_layout_count,
        "unique_layout_total": unique_layout_count,
        "remaining_in_queue": len(queue),
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    log("Tarama tamamlandi.")
    log(f"Toplam bulunan URL: {summary['discovered_total']}")
    log(f"Islenen URL: {summary['processed_total']}")
    log(f"Basarili screenshot: {summary['success_total']}")
    log(f"Hata: {summary['error_total']}")
    log(f"HTML olmayan atlanan: {summary['skipped_non_html_total']}")
    log(f"Duplicate layout atlanan: {summary['skipped_duplicate_layout_total']}")
    if config.unique_layout_only:
        log(f"Unique layout screenshot sayisi: {summary['unique_layout_total']}")
    log(f"Manifest: {manifest_path}")
    log(f"Ozet: {summary_path}")

    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verilen domain icin tum sayfalari gezip tam sayfa screenshot alan crawler."
    )
    parser.add_argument(
        "--start-url",
        required=True,
        help="Tarama icin baslangic URL (ornek: https://example.com).",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Cikti klasoru. Verilmezse ./captures/<domain>/<timestamp> kullanilir.",
    )
    parser.add_argument(
        "--timeout-ms",
        type=int,
        default=30000,
        help="Sayfa yukleme timeout degeri (milisaniye). Varsayilan: 30000.",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=0,
        help="Maksimum islenecek sayfa sayisi. 0 verilirse limitsiz.",
    )
    parser.add_argument(
        "--include-subdomains",
        action="store_true",
        help="Aktifse start domain altindaki subdomainler de taranir.",
    )
    parser.add_argument(
        "--no-sitemap",
        action="store_true",
        help="Sitemap kesfini devre disi birakir.",
    )
    parser.add_argument(
        "--no-link-crawl",
        action="store_true",
        help="Sayfa ici link kesfini devre disi birakir.",
    )
    parser.add_argument(
        "--unique-layout-only",
        action="store_true",
        help=(
            "Ayni HTML layout desenine sahip sayfalardan sadece ilkini screenshot alir. "
            "Digerleri manifest'e skipped_duplicate_layout olarak yazilir."
        ),
    )
    parser.add_argument(
        "--browser",
        choices=["auto", "chromium", "chrome", "edge"],
        default="auto",
        help=(
            "Kullanilacak tarayici. "
            "auto: once Chrome, sonra Edge, sonra Playwright Chromium."
        ),
    )
    parser.add_argument(
        "--executable-path",
        default=None,
        help="Opsiyonel tarayici executable yolu (chrome.exe/msedge.exe gibi).",
    )
    return parser


def parse_args(argv: list[str] | None = None) -> CrawlConfig:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if args.timeout_ms <= 0:
        parser.error("--timeout-ms 0'dan buyuk olmali.")
    if args.max_pages < 0:
        parser.error("--max-pages negatif olamaz.")

    output_dir = Path(args.output_dir).expanduser() if args.output_dir else None
    executable_path = (
        Path(args.executable_path).expanduser() if args.executable_path else None
    )

    return CrawlConfig(
        start_url=args.start_url,
        output_dir=output_dir,
        timeout_ms=args.timeout_ms,
        max_pages=args.max_pages,
        include_subdomains=args.include_subdomains,
        sitemap_enabled=not args.no_sitemap,
        link_crawl_enabled=not args.no_link_crawl,
        unique_layout_only=args.unique_layout_only,
        browser=args.browser,
        executable_path=executable_path,
    )


def main(argv: list[str] | None = None) -> int:
    try:
        config = parse_args(argv)
        return crawl(config)
    except KeyboardInterrupt:
        log("Kullanici tarafindan durduruldu.")
        return 130
    except Exception as exc:
        print(f"Hata: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
