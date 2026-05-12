#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "playwright>=1.54.0",
# ]
# ///
"""
sitetopng capture — interaktif screenshot aracı.

Kullanım:
  python capture.py open                       # tarayıcı aç + REPL
  python capture.py open --browser edge        # Edge ile aç
  python capture.py open --out-dir ./portfolio # varsayılan çıktı klasörü

  REPL içinde:
    capture --viewport 1440x900 --name 01-dashboard
    capture --selector "[data-testid=score-donut]" --name donut
    capture --viewport 375x812 --full-page --name mobile-scroll
    list           # açık sekmeleri listele
    help
    exit

  Ayrı terminalden (gelişmiş):
    python capture.py capture --viewport 1440x900 --out hero.png

uv ile:
    uv run capture.py open
    uv run capture.py capture --viewport 1200x900 --out hero.png
"""
from __future__ import annotations

import argparse
import asyncio
import json
import platform
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

DEFAULT_PORT = 9533  # uncommon, sitetopng'e ait. 9222 (Chrome default) MCP'lerle çakışıyor.
PROFILE_ROOT = Path.home() / ".sitetopng" / "profiles"
DEFAULT_OUT_PATTERN = "capture_{ts}.png"


def log(message: str) -> None:
    stamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{stamp}] {message}", flush=True)


def parse_viewport(value: str) -> tuple[int, int]:
    parts = value.lower().replace(" ", "").split("x")
    if len(parts) != 2:
        raise ValueError(f"Geçersiz viewport: {value!r}. Format: WIDTHxHEIGHT (ör: 1440x900)")
    try:
        return int(parts[0]), int(parts[1])
    except ValueError as exc:
        raise ValueError(f"Geçersiz viewport sayısı: {value!r}") from exc


def _disable_session_restore(profile_dir: Path) -> None:
    """Patch <profile>/Default/Preferences so Chrome doesn't reopen old tabs.

    Chrome's `session.restore_on_startup` defaults to 1 ("continue where I left
    off"), which makes stale tabs (NotebookLM, prior pages, etc.) reappear and
    pollute the target-tab heuristic. Force value 5 ("New Tab page") and clear
    any URL list.
    """
    default_dir = profile_dir / "Default"
    default_dir.mkdir(parents=True, exist_ok=True)
    prefs_path = default_dir / "Preferences"

    prefs: dict = {}
    if prefs_path.exists():
        try:
            prefs = json.loads(prefs_path.read_text(encoding="utf-8"))
        except Exception:
            prefs = {}

    session_prefs = prefs.get("session") or {}
    session_prefs["restore_on_startup"] = 5
    session_prefs["startup_urls"] = []
    prefs["session"] = session_prefs

    # Suppress the "Chrome didn't shut down correctly" bubble too.
    profile_prefs = prefs.get("profile") or {}
    profile_prefs["exit_type"] = "Normal"
    profile_prefs["exited_cleanly"] = True
    prefs["profile"] = profile_prefs

    try:
        prefs_path.write_text(json.dumps(prefs), encoding="utf-8")
    except Exception as exc:
        log(f"Uyarı: Preferences yazılamadı ({exc}); session restore aktif kalabilir.")


def find_browser_executable(browser: str, executable_path: str | None) -> str:
    if executable_path:
        target = Path(executable_path).expanduser()
        if not target.exists():
            raise RuntimeError(f"--executable-path bulunamadı: {target}")
        return str(target)

    system = platform.system()
    candidates: list[str] = []
    preferred: list[str] = []
    if browser == "auto":
        preferred = ["chrome", "edge", "chromium"]
    else:
        preferred = [browser]

    for choice in preferred:
        if choice == "chrome":
            if system == "Windows":
                candidates.extend(
                    [
                        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                        str(Path.home() / r"AppData\Local\Google\Chrome\Application\chrome.exe"),
                    ]
                )
            elif system == "Darwin":
                candidates.append(
                    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
                )
            else:
                candidates.extend(
                    [
                        "/usr/bin/google-chrome",
                        "/usr/bin/google-chrome-stable",
                        "/usr/bin/chrome",
                    ]
                )
        elif choice == "edge":
            if system == "Windows":
                candidates.extend(
                    [
                        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
                        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                    ]
                )
            elif system == "Darwin":
                candidates.append(
                    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"
                )
            else:
                candidates.append("/usr/bin/microsoft-edge")
        elif choice == "chromium":
            if system == "Windows":
                candidates.extend(
                    [
                        r"C:\Program Files\Chromium\Application\chrome.exe",
                    ]
                )
            elif system == "Darwin":
                candidates.append("/Applications/Chromium.app/Contents/MacOS/Chromium")
            else:
                candidates.extend(["/usr/bin/chromium", "/usr/bin/chromium-browser"])

    for candidate in candidates:
        if Path(candidate).exists():
            return candidate

    raise RuntimeError(
        "Tarayıcı executable bulunamadı. "
        "Çözüm: --executable-path ile yol verin (chrome.exe / msedge.exe gibi) "
        "veya Chrome / Edge yükleyin."
    )


def _probe_cdp(port: int, timeout: float = 1.5) -> dict | None:
    """CDP /json/version'u yokla. Yanit varsa dict, yoksa None."""
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/json/version", timeout=timeout
        ) as response:
            if response.status == 200:
                return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, ConnectionError, TimeoutError):
        pass
    return None


def _find_free_port(start: int, end: int = 9300) -> int | None:
    """Belirtilen araliktaki ilk CDP-bos port. Yoksa None."""
    for candidate in range(start, end + 1):
        if _probe_cdp(candidate, timeout=0.5) is None:
            return candidate
    return None


def wait_for_cdp(port: int, timeout: float = 20.0) -> dict:
    deadline = time.time() + timeout
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/json/version", timeout=1.5
            ) as response:
                if response.status == 200:
                    return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, ConnectionError) as exc:
            last_error = exc
        time.sleep(0.3)
    raise RuntimeError(
        f"CDP endpoint port {port} üzerinde hazır olmadı (timeout {timeout}s). "
        f"Son hata: {last_error}"
    )


@dataclass
class SessionState:
    out_dir: Path
    last_viewport: tuple[int, int] | None = None
    counter: int = 0
    name_used: set[str] = field(default_factory=set)


def resolve_out_path(args, session: SessionState) -> Path:
    if args.out:
        path = Path(args.out)
        if not path.is_absolute():
            path = session.out_dir / path
    elif args.name:
        safe = args.name.strip().replace(" ", "_")
        if not safe.lower().endswith(".png"):
            safe = f"{safe}.png"
        path = session.out_dir / safe
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        session.counter += 1
        path = session.out_dir / DEFAULT_OUT_PATTERN.format(ts=f"{timestamp}_{session.counter:02d}")

    path.parent.mkdir(parents=True, exist_ok=True)
    return path


async def _page_focus_state(page) -> tuple[bool, bool]:
    """Returns (has_focus, is_visible). Falls back to (False, False) on error."""
    try:
        result = await page.evaluate(
            "() => [typeof document!=='undefined' && document.hasFocus && document.hasFocus(), "
            "typeof document!=='undefined' && document.visibilityState === 'visible']"
        )
        return bool(result[0]), bool(result[1])
    except Exception:
        return False, False


async def _all_pages_across_contexts(browser_or_context):
    """Gather pages across every browser context (some Chrome configs put
    new tabs into a new context — the default-context-only loop misses them)."""
    pages: list = []
    # If passed a context, also enumerate sibling contexts via browser ref.
    contexts = []
    if hasattr(browser_or_context, "contexts"):
        contexts = list(browser_or_context.contexts)
    elif hasattr(browser_or_context, "browser") and browser_or_context.browser:
        contexts = list(browser_or_context.browser.contexts)
    else:
        contexts = [browser_or_context]
    for ctx in contexts:
        for page in ctx.pages:
            pages.append(page)
    return pages


async def pick_target_page(context_or_browser, args):
    """Hedef sekmeyi sec.

    Oncelik:
    1. --tab N      → tum context'lerde N'inci sayfa
    2. --url-contains STR → URL eslemesi
    3. Aksi: document.hasFocus() === True olan sekme (kullanicinin baktigi)
    4. Hala bulunmazsa: visibilityState === 'visible' olan
    5. Son care: en son olusturulan (pages[-1])
    """
    pages = await _all_pages_across_contexts(context_or_browser)
    if not pages:
        return None
    if args.tab is not None:
        if 0 <= args.tab < len(pages):
            return pages[args.tab]
        return None
    if args.url_contains:
        needle = args.url_contains.lower()
        for page in pages:
            if needle in (page.url or "").lower():
                return page
        return None

    # Auto: find the focused page first.
    focused: list = []
    visible: list = []
    for page in pages:
        has_focus, is_visible = await _page_focus_state(page)
        if has_focus:
            focused.append(page)
        elif is_visible:
            visible.append(page)
    if focused:
        return focused[-1]
    if visible:
        return visible[-1]
    return pages[-1]


async def do_capture(page, args, session: SessionState) -> None:
    try:
        await page.bring_to_front()
    except Exception:
        pass

    viewport_size: tuple[int, int] | None = None
    if args.viewport:
        try:
            viewport_size = parse_viewport(args.viewport)
        except ValueError as exc:
            print(f"[!] {exc}")
            return
    elif session.last_viewport and not args.no_viewport:
        viewport_size = session.last_viewport

    if viewport_size is not None:
        width, height = viewport_size
        try:
            await page.set_viewport_size({"width": width, "height": height})
            session.last_viewport = viewport_size
        except Exception as exc:
            print(f"[!] Viewport ayarlanamadı: {exc}")
            return

    if args.wait_selector:
        try:
            await page.wait_for_selector(args.wait_selector, timeout=10000)
        except Exception as exc:
            print(f"[!] --wait-selector bulunamadı ({args.wait_selector}): {exc}")
            return

    if args.wait_ms and args.wait_ms > 0:
        await page.wait_for_timeout(args.wait_ms)

    out_path = resolve_out_path(args, session)

    try:
        if args.selector:
            locator = page.locator(args.selector).first
            await locator.screenshot(path=str(out_path))
            mode = f"element({args.selector})"
        else:
            await page.screenshot(path=str(out_path), full_page=bool(args.full_page))
            mode = "full-page" if args.full_page else "viewport"
    except Exception as exc:
        print(f"[!] Screenshot hatası: {exc}")
        return

    vp = f"{viewport_size[0]}x{viewport_size[1]}" if viewport_size else "default"
    print(f"[+] {mode} @ {vp} -> {out_path}")


def build_capture_arg_parser(prog: str = "capture") -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog, add_help=True)
    parser.add_argument("--viewport", default=None, help="Viewport ölçüsü, örn: 1440x900")
    parser.add_argument(
        "--no-viewport",
        action="store_true",
        help="Önceki viewport hatırlamayı atla, tarayıcının mevcut boyutunu kullan.",
    )
    parser.add_argument(
        "--selector",
        default=None,
        help="CSS selector. Verilirse sadece o element'in shot'ı alınır.",
    )
    parser.add_argument(
        "--full-page",
        action="store_true",
        help="Viewport yerine tüm scroll'u içeren tam sayfa screenshot.",
    )
    parser.add_argument("--out", default=None, help="Çıktı dosya yolu (mutlak veya out-dir'e göre).")
    parser.add_argument(
        "--name",
        default=None,
        help="Çıktı için kısa ad (out-dir altında <name>.png olarak kaydedilir).",
    )
    parser.add_argument(
        "--url-contains",
        default=None,
        help="Hedef sekme seçimi: URL'i bu string'i içeren sekme.",
    )
    parser.add_argument(
        "--tab",
        type=int,
        default=None,
        help="Hedef sekme index (önce 'list' ile bak).",
    )
    parser.add_argument(
        "--wait-selector",
        default=None,
        help="Capture'dan önce bu selector görünene kadar bekle.",
    )
    parser.add_argument(
        "--wait-ms",
        type=int,
        default=0,
        help="Capture'dan önce bekle (ms).",
    )
    return parser


def print_repl_help() -> None:
    print(
        """
Komutlar:
  capture [--viewport WxH] [--selector CSS] [--full-page] [--name AD | --out DOSYA]
          [--url-contains STR | --tab N] [--wait-selector CSS] [--wait-ms N]
  list                      # tüm açık sekmeleri listele
  cd <dir>                  # varsayılan çıktı klasörünü değiştir
  help                      # bu mesaj
  exit / quit               # REPL'i kapat (tarayıcı açık kalır)

İpucu:
  - Bir kez --viewport verirsen sonraki capture'lar onu hatırlar.
  - --selector ile bölge çekersen viewport hâlâ önemlidir (responsive layout için).
  - Upwork portfolyo standardı: --viewport 1200x900
"""
    )


async def list_pages(context_or_browser) -> None:
    pages = await _all_pages_across_contexts(context_or_browser)
    if not pages:
        print("[!] Açık sekme yok.")
        return
    print("    idx  focus  visible  url")
    for index, page in enumerate(pages):
        url = (page.url or "").strip() or "about:blank"
        has_focus, is_visible = await _page_focus_state(page)
        focus_marker = " ●   " if has_focus else "  ·  "
        vis_marker = " ●     " if is_visible else "  ·    "
        print(f"    [{index:>2}] {focus_marker}{vis_marker}{url}")
    print()
    print("    Auto-pick (capture without --tab / --url-contains): focused tab → visible → last.")


async def repl_loop(browser, session: SessionState) -> None:
    loop = asyncio.get_event_loop()
    print_repl_help()
    print(f"Varsayılan çıktı klasörü: {session.out_dir}")
    while True:
        try:
            line = await loop.run_in_executor(None, lambda: input("sitetopng > "))
        except (EOFError, KeyboardInterrupt):
            print()
            return
        line = line.strip()
        if not line:
            continue

        try:
            tokens = shlex.split(line)
        except ValueError as exc:
            print(f"[!] Parse hatası: {exc}")
            continue

        cmd = tokens[0].lower()
        rest = tokens[1:]

        if cmd in ("exit", "quit"):
            return
        if cmd == "help":
            print_repl_help()
            continue
        if cmd == "list":
            await list_pages(browser)
            continue
        if cmd == "cd":
            if not rest:
                print(f"Şu an: {session.out_dir}")
                continue
            new_dir = Path(rest[0]).expanduser().resolve()
            new_dir.mkdir(parents=True, exist_ok=True)
            session.out_dir = new_dir
            print(f"[+] Çıktı klasörü: {session.out_dir}")
            continue
        if cmd == "capture":
            parser = build_capture_arg_parser()
            try:
                cap_args = parser.parse_args(rest)
            except SystemExit:
                continue
            page = await pick_target_page(browser, cap_args)
            if page is None:
                print("[!] Hedef sekme bulunamadı. 'list' ile aktif sekmeleri görebilirsin.")
                continue
            await do_capture(page, cap_args, session)
            continue

        print(f"[!] Bilinmeyen komut: {cmd!r}. 'help' yazın.")


async def cmd_open(args) -> int:
    from playwright.async_api import async_playwright

    chrome_path = find_browser_executable(args.browser, args.executable_path)

    profile_dir = (
        Path(args.profile_dir).expanduser()
        if args.profile_dir
        else PROFILE_ROOT / f"port-{args.port}"
    )
    if args.reset_profile and profile_dir.exists():
        import shutil

        log(f"Profil sıfırlanıyor (--reset-profile): {profile_dir}")
        shutil.rmtree(profile_dir, ignore_errors=True)
    profile_dir.mkdir(parents=True, exist_ok=True)

    # Port collision check — bu sitetopng'in #1 numaralı sorununu engeller.
    # NotebookLM MCP / Claude in Chrome / önceki sitetopng oturumu 9222'yi
    # tutuyor olabilir. Tutuyorsa, hangi tarayıcının orada olduğunu söyle ve
    # ya başka port seç (auto) ya da bağlanmayı reddet.
    existing = _probe_cdp(args.port, timeout=1.0)
    chosen_port = args.port
    if existing is not None:
        browser_name = existing.get("Browser", "unknown")
        log(
            f"⚠ Port {args.port} zaten kullanımda — başka bir Chrome (CDP) çalışıyor."
        )
        log(f"  Çalışan tarayıcı: {browser_name}")
        log(
            "  Bu büyük ihtimalle NotebookLM MCP, Claude-in-Chrome, ya da daha "
            "önceki bir sitetopng oturumu."
        )
        if args.auto_port:
            free = _find_free_port(args.port + 1)
            if free is None:
                print(
                    "[!] Boş CDP portu bulunamadı (9223–9300). "
                    "Diğer Chrome'u kapat veya --port ile manuel ver."
                )
                return 1
            chosen_port = free
            log(f"  --auto-port aktif → port {chosen_port} kullanılacak.")
        else:
            print(
                "\n[!] Aksi takdirde sitetopng yanlış Chrome'a bağlanır ve "
                "yanlış sekmeden screenshot alır.\n"
                "    Çözüm seçenekleri:\n"
                "      1) Diğer Chrome/CDP oturumunu kapat, tekrar dene\n"
                "      2) Farklı port: --port 9230\n"
                "      3) Otomatik boş port: --auto-port\n"
            )
            return 1

    # Disable Chrome session restore (prevents previous tabs/NotebookLM/etc.
    # from reopening on launch). Writes to <profile>/Default/Preferences before
    # Chrome reads it.
    _disable_session_restore(profile_dir)

    out_dir = Path(args.out_dir).expanduser().resolve() if args.out_dir else Path.cwd()
    out_dir.mkdir(parents=True, exist_ok=True)

    chrome_args = [
        chrome_path,
        f"--remote-debugging-port={chosen_port}",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-session-crashed-bubble",
        "--disable-features=AutomationControlled,InfiniteSessionRestore",
        # --restore-last-session=false is a switch some Chromium forks accept.
        # Harmless on stock Chrome (unknown switches are ignored).
        "--restore-last-session=false",
        args.start_url or "about:blank",
    ]

    log(f"Tarayıcı: {chrome_path}")
    log(f"CDP port: {chosen_port}")
    log(f"Profil: {profile_dir}")
    log(f"Çıktı klasörü: {out_dir}")

    creation_flags = 0
    if platform.system() == "Windows":
        creation_flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

    browser_proc = subprocess.Popen(chrome_args, creationflags=creation_flags)

    try:
        wait_for_cdp(chosen_port, timeout=25.0)
    except RuntimeError as exc:
        browser_proc.terminate()
        print(f"[!] {exc}")
        return 1

    log(f"CDP hazır (port {chosen_port}). Playwright bağlanıyor...")
    session = SessionState(out_dir=out_dir)

    async with async_playwright() as playwright:
        try:
            browser = await playwright.chromium.connect_over_cdp(
                f"http://127.0.0.1:{chosen_port}"
            )
        except Exception as exc:
            browser_proc.terminate()
            print(f"[!] CDP bağlantı hatası: {exc}")
            return 1

        contexts = browser.contexts
        if not contexts:
            print("[!] Mevcut tarayıcı context'i bulunamadı.")
            browser_proc.terminate()
            return 1
        context = contexts[0]

        print(
            "\n[+] Tarayıcı hazır. Giriş yap, istediğin sayfaya git, "
            "sonra bu terminalden 'capture' komutu çalıştır.\n"
        )

        try:
            await repl_loop(browser, session)
        finally:
            try:
                await browser.close()
            except Exception:
                pass

    log("REPL kapandı. Tarayıcı hâlâ açık olabilir.")
    answer = input("Tarayıcıyı da kapatayım mı? [Y/n]: ").strip().lower()
    if answer in ("", "y", "yes", "e", "evet"):
        browser_proc.terminate()
        try:
            browser_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            browser_proc.kill()
        log("Tarayıcı kapatıldı.")
    else:
        log(f"Tarayıcı açık bırakıldı (PID {browser_proc.pid}).")
    return 0


async def cmd_capture(args) -> int:
    from playwright.async_api import async_playwright

    try:
        wait_for_cdp(args.port, timeout=3.0)
    except RuntimeError as exc:
        print(f"[!] {exc}")
        print("    Önce başka bir terminalde 'python capture.py open' çalıştırın.")
        return 1

    out_dir = Path(args.out_dir).expanduser().resolve() if args.out_dir else Path.cwd()
    session = SessionState(out_dir=out_dir)

    async with async_playwright() as playwright:
        try:
            browser = await playwright.chromium.connect_over_cdp(
                f"http://127.0.0.1:{args.port}"
            )
        except Exception as exc:
            print(f"[!] CDP bağlantı hatası: {exc}")
            return 1

        contexts = browser.contexts
        if not contexts:
            print("[!] Aktif context yok.")
            return 1

        page = await pick_target_page(browser, args)
        if page is None:
            print("[!] Hedef sekme bulunamadı.")
            await browser.close()
            return 1

        await do_capture(page, args, session)
        try:
            await browser.close()
        except Exception:
            pass

    return 0


def build_main_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sitetopng",
        description=(
            "İnteraktif screenshot aracı. "
            "Tarayıcıyı 'open' ile aç, manuel giriş yap, sonra REPL'den 'capture' çalıştır."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    open_parser = subparsers.add_parser(
        "open", help="Tarayıcıyı CDP açık şekilde başlat ve REPL'e gir."
    )
    open_parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    open_parser.add_argument(
        "--browser",
        choices=["auto", "chrome", "edge", "chromium"],
        default="auto",
    )
    open_parser.add_argument("--executable-path", default=None)
    open_parser.add_argument(
        "--profile-dir",
        default=None,
        help=f"Tarayıcı user-data-dir (varsayılan: {PROFILE_ROOT}/port-<PORT>).",
    )
    open_parser.add_argument(
        "--reset-profile",
        action="store_true",
        help="Profil klasörünü açmadan önce sil (NotebookLM gibi eski tab'ları temizler).",
    )
    open_parser.add_argument(
        "--auto-port",
        action="store_true",
        help="Port çakışırsa otomatik bir sonraki boş portu kullan (9223–9300).",
    )
    open_parser.add_argument(
        "--out-dir",
        default=None,
        help="REPL'deki capture'ların varsayılan çıktı klasörü (varsayılan: CWD).",
    )
    open_parser.add_argument(
        "--start-url",
        default=None,
        help="Tarayıcı açıldığında gidilecek ilk URL.",
    )

    capture_parser = subparsers.add_parser(
        "capture",
        help="Açık tarayıcıdan (CDP attach) tek seferlik screenshot al.",
    )
    capture_parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    capture_parser.add_argument("--viewport", default=None)
    capture_parser.add_argument("--no-viewport", action="store_true")
    capture_parser.add_argument("--selector", default=None)
    capture_parser.add_argument("--full-page", action="store_true")
    capture_parser.add_argument("--out", default=None)
    capture_parser.add_argument("--name", default=None)
    capture_parser.add_argument("--out-dir", default=None)
    capture_parser.add_argument("--url-contains", default=None)
    capture_parser.add_argument("--tab", type=int, default=None)
    capture_parser.add_argument("--wait-selector", default=None)
    capture_parser.add_argument("--wait-ms", type=int, default=0)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_main_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "open":
            return asyncio.run(cmd_open(args))
        if args.command == "capture":
            return asyncio.run(cmd_capture(args))
    except KeyboardInterrupt:
        print()
        log("Kullanıcı tarafından durduruldu.")
        return 130
    except Exception as exc:
        print(f"Hata: {exc}", file=sys.stderr)
        return 1
    parser.error(f"Bilinmeyen komut: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
