"""
Shared helpers for driving the Lokalise web app (Team settings > Translation memory)
with Playwright.

Lokalise has no public API for translation memories, so the scripts automate the
same clicks a team admin would make in the browser.

Everything that depends on Lokalise's UI wording lives in the "UI TEXT" block below.
If Lokalise renames a button, that is the only place you should need to edit.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from playwright.sync_api import BrowserContext, Page, sync_playwright
from playwright.sync_api import TimeoutError as PWTimeout

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
BASE_URL = "https://app.lokalise.com/"
HOME = Path(os.environ.get("LOKALISE_TM_HOME", ".lokalise_tm"))
PROFILE_DIR = HOME / "browser_profile"  # holds your login cookies - keep private
CONFIG_FILE = HOME / "config.json"      # remembers the Translation memory page URL
DEBUG_DIR = HOME / "debug"

# --------------------------------------------------------------------------- #
# UI TEXT (regexes, case-insensitive). Edit here if Lokalise changes its labels.
# --------------------------------------------------------------------------- #
DOWNLOAD_RX = r"download.*tmx|tmx.*download"
UPLOAD_RX = r"upload.*tmx|tmx.*upload"
DOWNLOAD_CONFIRM_RX = re.compile(r"^\s*(download|export|confirm|ok|save|continue)\b", re.I)
UPLOAD_CONFIRM_RX = re.compile(r"^\s*(upload|import|confirm|ok|save|continue)\b", re.I)
CANCEL_RX = re.compile(r"cancel|close|back|×", re.I)
UPLOAD_SUCCESS_RX = re.compile(
    r"success|uploaded|imported|processed|queued|in progress|processing", re.I
)
LOGIN_URL_HINTS = ("/login", "/signin", "/sign-in", "/auth", "/signup", "/sso")

DOWNLOAD_TIMEOUT_MS = 180_000
UPLOAD_SUCCESS_TIMEOUT_MS = 45_000


@dataclass
class TM:
    idx: int
    name: str


# --------------------------------------------------------------------------- #
# Small utilities
# --------------------------------------------------------------------------- #
def safe_name(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', "_", name).strip() or "tm"


def timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def choose(items: list[str], title: str, multi: bool = False) -> list[int]:
    """Numbered prompt. Returns zero-based indexes."""
    print(f"\n{title}")
    for i, item in enumerate(items, 1):
        print(f"  {i:>2}. {item}")
    hint = "numbers separated by commas, or 'all'" if multi else "number"
    while True:
        raw = input(f"Select ({hint}): ").strip().lower()
        if multi and raw == "all":
            return list(range(len(items)))
        try:
            parts = [p for p in raw.replace(" ", "").split(",") if p]
            picks = [int(p) - 1 for p in parts] if multi else [int(raw) - 1]
            if picks and all(0 <= p < len(items) for p in picks):
                return sorted(set(picks))
        except ValueError:
            pass
        print("Invalid selection, try again.")


def add_common_args(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--headed", action="store_true",
                    help="show the browser window (useful for debugging)")
    ap.add_argument("--reset", action="store_true",
                    help="forget the saved login session and Translation memory URL")


def load_cfg() -> dict:
    try:
        return json.loads(CONFIG_FILE.read_text())
    except (OSError, ValueError):
        return {}


def save_cfg(cfg: dict) -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))


def reset_session() -> None:
    shutil.rmtree(PROFILE_DIR, ignore_errors=True)
    CONFIG_FILE.unlink(missing_ok=True)
    print("Saved session and settings cleared.")


def dump_debug(page: Page, tag: str) -> Path:
    """Save a screenshot + HTML so selectors can be fixed quickly."""
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    base = DEBUG_DIR / f"{tag}_{timestamp()}"
    try:
        page.screenshot(path=str(base.with_suffix(".png")), full_page=True)
        base.with_suffix(".html").write_text(page.content(), encoding="utf-8")
    except Exception:  # noqa: BLE001 - debugging aid only
        pass
    return base


# --------------------------------------------------------------------------- #
# Session / authentication
# --------------------------------------------------------------------------- #
def _launch(pw, headless: bool) -> BrowserContext:
    HOME.mkdir(parents=True, exist_ok=True)
    return pw.chromium.launch_persistent_context(
        str(PROFILE_DIR),
        headless=headless,
        accept_downloads=True,
        viewport={"width": 1440, "height": 900},
    )


def _goto(page: Page, url: str) -> None:
    page.goto(url, wait_until="domcontentloaded")
    try:
        page.wait_for_load_state("networkidle", timeout=10_000)
    except PWTimeout:
        pass
    page.wait_for_timeout(1500)  # SPA redirects can lag behind load events


def _is_login_page(page: Page) -> bool:
    url = page.url.lower()
    if any(h in url for h in LOGIN_URL_HINTS):
        return True
    return page.locator("input[type=password]").count() > 0


def _prefill_credentials(page: Page) -> None:
    """Best effort only. SSO / 2FA must be completed by hand in the window."""
    email, pwd = os.environ.get("LOKALISE_EMAIL"), os.environ.get("LOKALISE_PASSWORD")
    try:
        if email and page.locator("input[type=email], input[name=email]").count():
            page.locator("input[type=email], input[name=email]").first.fill(email)
        if pwd and page.locator("input[type=password]").count():
            page.locator("input[type=password]").first.fill(pwd)
    except Exception:  # noqa: BLE001
        pass


def _open(pw, headed: bool) -> tuple[BrowserContext, Page]:
    cfg = load_cfg()
    target_url = cfg.get("tm_url") or BASE_URL
    need_headed = headed or not PROFILE_DIR.exists() or not cfg.get("tm_url")

    ctx = _launch(pw, headless=not need_headed)
    page = ctx.new_page()
    _goto(page, target_url)

    if _is_login_page(page):
        if not need_headed:  # saved session expired -> need a visible window
            ctx.close()
            ctx = _launch(pw, headless=False)
            page = ctx.new_page()
            _goto(page, target_url)
        _prefill_credentials(page)
        input("\n>>> Log in to Lokalise in the browser window (SSO / 2FA are fine).\n"
              "    When you can see your projects/dashboard, press Enter here... ")
        _goto(page, target_url)

    if not cfg.get("tm_url"):
        input("\n>>> In the browser window, open  Team settings > Translation memory  "
              "(the page listing your TMX storages).\n    Then press Enter here... ")
        cfg["tm_url"] = page.url
        save_cfg(cfg)
        print(f"Saved Translation memory page: {page.url}")

    return ctx, page


@contextmanager
def lokalise_session(headed: bool = False, reset: bool = False):
    """Yields a Playwright page sitting on the Translation memory settings page."""
    if reset:
        reset_session()
    with sync_playwright() as pw:
        ctx, page = _open(pw, headed)
        try:
            yield page
        finally:
            ctx.close()


# --------------------------------------------------------------------------- #
# Reading the TM list
# --------------------------------------------------------------------------- #
# Finds every visible "Download as TMX" control, tags it with data-tm-idx and works
# out the row's TM name (first line of text in the smallest ancestor that contains
# exactly one download control).
_SCAN_JS = r"""
(rxSrc) => {
  const rx = new RegExp(rxSrc, "i");
  const noise = /^(delete|clear|upload|download|edit|rename|more|actions?|entries)/i;
  document.querySelectorAll("[data-tm-idx]").forEach(e => e.removeAttribute("data-tm-idx"));
  const label = el => (el.getAttribute("aria-label") || el.getAttribute("title") ||
                       el.innerText || "").trim();
  const visible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  let cands = [...document.querySelectorAll(
      "button, a, [role=button], [role=menuitem], [role=link], [title], [aria-label]")]
    .filter(el => rx.test(label(el)) && visible(el));
  cands = cands.filter(el => !cands.some(o => o !== el && o.contains(el)));
  return cands.map((el, i) => {
    el.setAttribute("data-tm-idx", String(i));
    let name = "";
    let row = el.parentElement;
    for (let d = 0; d < 8 && row; d++, row = row.parentElement) {
      if (cands.filter(c => row.contains(c)).length > 1) break;
      const walker = document.createTreeWalker(row, NodeFilter.SHOW_TEXT);
      let node;
      while ((node = walker.nextNode())) {
        const t = node.textContent.trim();
        const p = node.parentElement;
        if (!t || rx.test(t) || noise.test(t)) continue;
        if (p.closest("button, a, [role=button], [role=menuitem], [role=link]")) continue;
        name = t;
        break;
      }
      if (name) break;
    }
    return { idx: i, name };
  });
}
"""


def list_tms(page: Page, retries: int = 10) -> list[TM]:
    for _ in range(retries):
        found = page.evaluate(_SCAN_JS, DOWNLOAD_RX)
        if found:
            return [TM(f["idx"], f["name"] or f"TM #{f['idx'] + 1}") for f in found]
        page.wait_for_timeout(1500)
    where = dump_debug(page, "no_tm_found")
    raise RuntimeError(
        "Could not find any 'Download as TMX' controls on the page.\n"
        "  - Are you on Team settings > Translation memory (Advanced plan or above)?\n"
        "  - Try --reset, or adjust DOWNLOAD_RX in lokalise_ui.py.\n"
        f"  Debug files: {where}.png / .html"
    )


def _find(page: Page, name: str) -> TM:
    for tm in list_tms(page, retries=4):
        if tm.name == name:
            return tm
    raise RuntimeError(f"TM '{name}' not found on the page.")


def _visible_dialog(page: Page):
    return (page.locator("[role=dialog]")
            .or_(page.locator("[aria-modal=true]"))
            .or_(page.locator("[class*=modal]:not(body):not(html)"))
            .filter(visible=True))


def _click_dialog_button(dlg, confirm_rx: re.Pattern) -> None:
    btn = dlg.get_by_role("button", name=confirm_rx)
    if btn.count() == 0:
        btn = dlg.locator("button").filter(has_not_text=CANCEL_RX)
    btn.last.click()


# --------------------------------------------------------------------------- #
# Download
# --------------------------------------------------------------------------- #
def download_tm(page: Page, tm_name: str, dest_dir: Path, filename: str | None = None) -> Path:
    """Click 'Download as TMX file' for one TM (all languages) and save it."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    tm = _find(page, tm_name)
    button = page.locator(f'[data-tm-idx="{tm.idx}"]').first

    try:
        with page.expect_download(timeout=DOWNLOAD_TIMEOUT_MS) as info:
            button.click()
            dlg = _visible_dialog(page)
            has_dialog = True
            try:  # Lokalise asks "all languages or a language pair?" - keep the default
                dlg.last.wait_for(state="visible", timeout=4000)
            except PWTimeout:
                has_dialog = False  # download may have started directly
            if has_dialog:
                _click_dialog_button(dlg.last, DOWNLOAD_CONFIRM_RX)
        download = info.value
    except PWTimeout:
        where = dump_debug(page, "download_failed")
        raise RuntimeError(
            f"No download started for '{tm_name}'. Large TMs may be delivered another way "
            f"(e.g. by email), or the dialog button text changed (DOWNLOAD_CONFIRM_RX).\n"
            f"  Debug files: {where}.png / .html"
        ) from None

    target = dest_dir / (filename or download.suggested_filename or f"{safe_name(tm_name)}.tmx")
    download.save_as(target)
    return target


# --------------------------------------------------------------------------- #
# Upload
# --------------------------------------------------------------------------- #
def upload_tmx(page: Page, tmx_path: Path) -> None:
    """Click 'Upload TMX file' and hand it the file. Lokalise merges into the TM
    whose name equals the file name (per Lokalise docs)."""
    rx = re.compile(UPLOAD_RX, re.I)
    button = (page.get_by_role("button", name=rx)
              .or_(page.get_by_role("link", name=rx))
              .or_(page.get_by_text(rx))).first
    try:
        with page.expect_file_chooser(timeout=8000) as chooser:
            button.click()
        chooser.value.set_files(str(tmx_path))
    except PWTimeout:
        inputs = page.locator("input[type=file]")
        if inputs.count() == 0:
            where = dump_debug(page, "upload_failed")
            raise RuntimeError(
                "Could not find the upload control (UPLOAD_RX / file input).\n"
                f"  Debug files: {where}.png / .html"
            ) from None
        inputs.first.set_input_files(str(tmx_path))

    # Some flows show a confirmation dialog after choosing the file.
    try:
        dlg = _visible_dialog(page)
        dlg.last.wait_for(state="visible", timeout=3000)
        _click_dialog_button(dlg.last, UPLOAD_CONFIRM_RX)
    except PWTimeout:
        pass

    try:
        page.get_by_text(UPLOAD_SUCCESS_RX).first.wait_for(
            state="visible", timeout=UPLOAD_SUCCESS_TIMEOUT_MS)
    except PWTimeout:
        where = dump_debug(page, "upload_unconfirmed")
        print(f"  ! No success message detected (screenshot: {where}.png). "
              "Verify in the Lokalise UI.", file=sys.stderr)
    page.wait_for_timeout(2000)
