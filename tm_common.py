"""
Shared code for the Phrase and Smartcat translation-memory tools.

Each provider (phrase_client.py / smartcat_client.py) implements a small interface:

    name                      short id, e.g. "phrase"
    add_args(ap)              provider-specific CLI options
    authenticate(args)        log in / verify credentials
    list_tms() -> [TMInfo]
    export_tm(tm, dest_path)  write the TM as TMX to dest_path
    import_tm(tm, tmx, args)  merge a TMX file into the TM, return a summary string
    describe_mode(args)       text describing what import will do (merge vs replace)
    confirm_extra(args, tm)   optional extra safety prompt; return False to abort

run_download() and run_reimport() below are the two scripts' shared implementations.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import re
import requests

XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"


# --------------------------------------------------------------------------- #
# Basic types / helpers
# --------------------------------------------------------------------------- #
@dataclass
class TMInfo:
    id: str
    name: str
    source: str = ""
    targets: list[str] = field(default_factory=list)


class ProviderError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def safe_name(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', "_", name).strip() or "tm"


def timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def choose(items: list[str], title: str, multi: bool = False) -> list[int]:
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


def api_call(session: requests.Session, method: str, url: str, retries: int = 4, **kw):
    """HTTP request with retry on 429/5xx and readable errors."""
    kw.setdefault("timeout", (15, 300))
    data = kw.get("data")
    for attempt in range(retries + 1):
        if hasattr(data, "seek"):
            data.seek(0)
        try:
            r = session.request(method, url, **kw)
        except requests.ConnectionError as exc:
            if attempt == retries:
                raise ProviderError(f"{method} {url}: connection failed ({exc})") from exc
            time.sleep(2 ** attempt)
            continue
        if r.status_code == 429 or r.status_code >= 500:
            if attempt < retries:
                wait = float(r.headers.get("Retry-After") or 2 ** (attempt + 1))
                time.sleep(min(wait, 60))
                continue
        if not r.ok:
            raise ProviderError(f"{method} {url} -> HTTP {r.status_code}: {r.text[:300]}", r.status_code)
        return r
    raise ProviderError(f"{method} {url}: retries exhausted")  # pragma: no cover


def stream_to_file(response: requests.Response, dest: Path) -> Path:
    """Write a streamed response atomically (.part -> final)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with open(tmp, "wb") as fh:
        for chunk in response.iter_content(chunk_size=1 << 20):
            fh.write(chunk)
    tmp.replace(dest)
    return dest


# --------------------------------------------------------------------------- #
# TMX handling
# --------------------------------------------------------------------------- #
def analyse(path: Path):
    """Returns (root, valid_tus, skipped_count, languages)."""
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        sys.exit(f"Not valid XML: {path} ({exc})")
    body = root.find("body")
    if root.tag != "tmx" or body is None:
        sys.exit(f"Not a TMX file (missing <tmx>/<body>): {path}")

    good, skipped, langs = [], 0, set()
    for tu in body.findall("tu"):
        tu_langs = set()
        for tuv in tu.findall("tuv"):
            lang = (tuv.get(XML_LANG) or tuv.get("lang") or "").strip()
            if lang and tuv.find("seg") is not None:
                tu_langs.add(lang)
        if len(tu_langs) >= 2:
            good.append(tu)
            langs |= tu_langs
        else:
            skipped += 1
    return root, good, skipped, langs


def write_parts(root, tus, filename: str, work_dir: Path, max_bytes: int | None) -> list[Path]:
    header = root.find("header")
    if header is None:
        header = ET.Element("header", {
            "creationtool": "tm-api-tools", "creationtoolversion": "1.0",
            "segtype": "sentence", "o-tmf": "unknown", "adminlang": "en",
            "srclang": "*all*", "datatype": "plaintext"})
    chunks, cur, size = [], [], 0
    for tu in tus:
        s = len(ET.tostring(tu, encoding="utf-8"))
        if max_bytes and cur and size + s > max_bytes:
            chunks.append(cur)
            cur, size = [], 0
        cur.append(tu)
        size += s
    if cur:
        chunks.append(cur)

    paths = []
    for n, chunk in enumerate(chunks, 1):
        folder = work_dir / f"part{n}"
        folder.mkdir(parents=True, exist_ok=True)
        new_root = ET.Element("tmx", root.attrib or {"version": "1.4"})
        new_root.append(header)
        ET.SubElement(new_root, "body").extend(chunk)
        p = folder / filename
        ET.ElementTree(new_root).write(p, encoding="utf-8", xml_declaration=True)
        paths.append(p)
    return paths


def _norm_lang(lang: str) -> str:
    return lang.strip().lower().replace("_", "-")


def language_warnings(tmx_langs: set[str], tm: TMInfo) -> list[str]:
    """Compare TMX languages with the target TM's languages (if the API told us)."""
    tm_langs = {_norm_lang(x) for x in [tm.source, *tm.targets] if x}
    if not tm_langs:
        return []
    tm_bases = {x.split("-")[0] for x in tm_langs}
    out = []
    for lang in sorted(tmx_langs):
        n = _norm_lang(lang)
        if n in tm_langs:
            continue
        if n.split("-")[0] in tm_bases:
            out.append(f"'{lang}' matches a TM language only by base language (regional code differs)")
        else:
            out.append(f"'{lang}' is not a language of TM '{tm.name}' ({', '.join(sorted(tm_langs))}) "
                       "- those entries may be skipped")
    return out


def pick_local_file(folder: Path) -> Path:
    files = sorted(folder.glob("*.tmx"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        sys.exit(f"No .tmx files found in {folder}. Run the download script first or pass --file.")
    names = {}
    try:
        for e in json.loads((folder / "manifest.json").read_text()):
            names[e["file"]] = e.get("tm_name", "")
    except (OSError, ValueError, KeyError):
        pass
    labels = []
    for f in files:
        src = f"  (from TM '{names[f.name]}')" if names.get(f.name) else ""
        labels.append(f"{f.name}  [{f.stat().st_size / 1_048_576:.1f} MB]{src}")
    return files[choose(labels, f"Which local TMX file do you want to re-import? ({folder})")[0]]


def update_manifest(out: Path, entry: dict) -> None:
    mf = out / "manifest.json"
    try:
        data = json.loads(mf.read_text())
    except (OSError, ValueError):
        data = []
    data.append(entry)
    mf.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def _find_tm(tms: list[TMInfo], key: str) -> list[TMInfo]:
    return [t for t in tms if t.name == key or t.id == key]


# --------------------------------------------------------------------------- #
# Script 1: authenticate, choose TMs, download
# --------------------------------------------------------------------------- #
def run_download(provider, doc: str) -> int:
    ap = argparse.ArgumentParser(description=doc, formatter_class=argparse.RawTextHelpFormatter)
    provider.add_args(ap)
    ap.add_argument("--out", default=f"tm_backups/{provider.name}", help="output folder")
    ap.add_argument("--all", action="store_true", help="download all translation memories")
    ap.add_argument("--tm", action="append", metavar="NAME_OR_ID",
                    help="download this TM (exact name or id; repeatable)")
    args = ap.parse_args()
    out = Path(args.out)

    provider.authenticate(args)
    tms = provider.list_tms()
    if not tms:
        print("No translation memories visible to this account.")
        return 1
    print(f"Found {len(tms)} translation memor{'y' if len(tms) == 1 else 'ies'}.")

    if args.all:
        chosen = tms
    elif args.tm:
        chosen = []
        for key in args.tm:
            hits = _find_tm(tms, key)
            if not hits:
                print(f"Not found: {key}", file=sys.stderr)
            chosen.extend(hits)
    else:
        labels = [f"{t.name}  [{t.source or '?'} -> {', '.join(t.targets) or '?'}]" for t in tms]
        chosen = [tms[i] for i in choose(labels, "Which TM(s) do you want to download?", multi=True)]
    if not chosen:
        print("Nothing to download.")
        return 1

    failures = 0
    for tm in chosen:
        print(f"\nDownloading '{tm.name}' ...")
        dest = out / f"{safe_name(tm.name)}__{timestamp()}.tmx"
        try:
            provider.export_tm(tm, dest)
        except (ProviderError, OSError) as exc:
            failures += 1
            print(f"  FAILED: {exc}", file=sys.stderr)
            continue
        print(f"  saved {dest}  ({dest.stat().st_size / 1_048_576:.1f} MB)")
        update_manifest(out, {"provider": provider.name, "tm_id": tm.id, "tm_name": tm.name,
                              "file": dest.name, "downloaded_at": timestamp()})
    print(f"\nDone. {len(chosen) - failures}/{len(chosen)} downloaded to {out.resolve()}")
    return 1 if failures else 0


# --------------------------------------------------------------------------- #
# Script 2: choose local TMX + target TM, back up, import (merge)
# --------------------------------------------------------------------------- #
def run_reimport(provider, doc: str) -> int:
    ap = argparse.ArgumentParser(description=doc, formatter_class=argparse.RawTextHelpFormatter)
    provider.add_args(ap)
    ap.add_argument("--dir", default=f"tm_backups/{provider.name}", help="folder with downloaded TMX files")
    ap.add_argument("--file", help="TMX file to import (skips the picker)")
    ap.add_argument("--target", metavar="NAME_OR_ID", help="target TM (exact name or id; skips the picker)")
    ap.add_argument("--max-mb", type=float, default=0,
                    help="split the upload into parts of at most this many MB (default: no splitting)")
    ap.add_argument("--no-backup", action="store_true", help="skip backing up the target TM first")
    ap.add_argument("--dry-run", action="store_true", help="validate and show the plan; import nothing")
    ap.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    args = ap.parse_args()

    folder = Path(args.dir)
    tmx = Path(args.file) if args.file else pick_local_file(folder)
    if not tmx.is_file():
        sys.exit(f"File not found: {tmx}")

    root, tus, skipped, langs = analyse(tmx)
    print(f"\n{tmx.name}: {len(tus)} valid translation units, languages: {', '.join(sorted(langs)) or '-'}")
    if skipped:
        print(f"  ! {skipped} unit(s) lack text in two languages and will be left out of the upload.")
    if not tus:
        sys.exit("Nothing to import.")

    provider.authenticate(args)
    tms = provider.list_tms()
    if not tms:
        sys.exit("No translation memories visible to this account.")

    if args.target:
        hits = _find_tm(tms, args.target)
        if len(hits) != 1:
            sys.exit(f"Target '{args.target}' matched {len(hits)} TMs. Use the id, or pick interactively.")
        target = hits[0]
    else:
        labels = [f"{t.name}  [{t.source or '?'} -> {', '.join(t.targets) or '?'}]" for t in tms]
        target = tms[choose(labels, "Into which TM should this be merged?")[0]]

    for w in language_warnings(langs, target):
        print(f"  ! {w}")

    max_bytes = int(args.max_mb * 1_048_576) if args.max_mb else None
    with tempfile.TemporaryDirectory() as tmp:
        if skipped == 0 and not max_bytes:
            parts = [tmx]  # upload the original file untouched
        else:
            parts = write_parts(root, tus, tmx.name, Path(tmp), max_bytes)

        print(f"\nPlan: {provider.describe_mode(args)}")
        print(f"      {len(tus)} units from '{tmx.name}'  ->  TM '{target.name}'  in {len(parts)} upload(s)")
        if args.dry_run:
            print("Dry run - nothing imported.")
            return 0
        if not args.yes:
            if input("Proceed? [y/N] ").strip().lower() != "y":
                print("Aborted.")
                return 1
            if not provider.confirm_extra(args, target):
                print("Aborted.")
                return 1

        if not args.no_backup:
            print(f"\nBacking up '{target.name}' first ...")
            bak = folder / "pre_merge_backups" / f"{safe_name(target.name)}__pre-merge_{timestamp()}.tmx"
            try:
                provider.export_tm(target, bak)
                print(f"  backup saved: {bak}")
            except (ProviderError, OSError) as exc:
                sys.exit(f"Backup failed, aborting (use --no-backup to override): {exc}")

        for n, part in enumerate(parts, 1):
            print(f"\nImporting part {n}/{len(parts)} ...")
            try:
                print("  " + provider.import_tm(target, part, args))
            except ProviderError as exc:
                print(f"  FAILED: {exc}", file=sys.stderr)
                return 2

    print("\nDone. Open the TM in the web app to spot-check the merged entries.")
    return 0
