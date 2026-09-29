#!/usr/bin/env python3
"""
Script 2 - re-import a TMX file into a chosen Lokalise translation memory (merge).

    python tm_reimport.py                                   # fully interactive
    python tm_reimport.py --file tm_backups/X.tmx --target "Marketing TM"
    python tm_reimport.py --dry-run                         # validate + show plan, no upload

How the merge works (per Lokalise docs): uploading a TMX file whose *file name equals
the name of an existing TM* adds its entries to that TM. This script therefore:

  1. lets you pick a local TMX file (from ./tm_backups by default),
  2. validates it (every <tu> needs a <tuv> for at least two languages) and splits it
     if it is over Lokalise's 100 MB upload limit,
  3. lets you pick the target TM in Lokalise,
  4. downloads a safety backup of the target TM (skip with --no-backup),
  5. uploads the file(s) renamed to "<target TM name>.tmx",
  6. re-reads the TM list and warns if a NEW TM appeared (= name mismatch, no merge).
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from lokalise_ui import (add_common_args, choose, download_tm, list_tms,
                         lokalise_session, safe_name, timestamp, upload_tmx)

XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
MAX_BYTES = 90 * 1024 * 1024  # stay under Lokalise's 100 MB limit


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


def write_parts(root: ET.Element, tus: list[ET.Element], filename: str, work_dir: Path) -> list[Path]:
    """Write TUs to one or more TMX files that all share the same file name
    (each in its own sub-folder), because the file name decides the merge target."""
    header = root.find("header")
    if header is None:
        header = ET.Element("header", {
            "creationtool": "lokalise-tm-tools", "creationtoolversion": "1.0",
            "segtype": "sentence", "o-tmf": "unknown", "adminlang": "en",
            "srclang": "*all*", "datatype": "plaintext",
        })

    chunks, cur, size = [], [], 0
    for tu in tus:
        s = len(ET.tostring(tu, encoding="utf-8"))
        if cur and size + s > MAX_BYTES:
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


# --------------------------------------------------------------------------- #
# Local file picker
# --------------------------------------------------------------------------- #
def pick_local_file(folder: Path) -> Path:
    files = sorted(folder.glob("*.tmx"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        sys.exit(f"No .tmx files found in {folder}. Run tm_download.py first or pass --file.")

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


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    add_common_args(ap)
    ap.add_argument("--dir", default="tm_backups", help="folder with downloaded TMX files")
    ap.add_argument("--file", help="TMX file to import (skips the picker)")
    ap.add_argument("--target", help="exact name of the target TM in Lokalise (skips the picker)")
    ap.add_argument("--no-backup", action="store_true", help="skip backing up the target TM first")
    ap.add_argument("--dry-run", action="store_true", help="validate and show the plan; upload nothing")
    ap.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    args = ap.parse_args()

    folder = Path(args.dir)
    tmx = Path(args.file) if args.file else pick_local_file(folder)
    if not tmx.is_file():
        sys.exit(f"File not found: {tmx}")

    root, tus, skipped, langs = analyse(tmx)
    print(f"\n{tmx.name}: {len(tus)} valid translation units, languages: {', '.join(sorted(langs)) or '-'}")
    if skipped:
        print(f"  ! {skipped} unit(s) lack a <tuv> for two languages and will be skipped "
              "(Lokalise would reject the whole file).")
    if not tus:
        sys.exit("Nothing to import.")

    with lokalise_session(headed=args.headed, reset=args.reset) as page:
        tms = list_tms(page)
        names_before = [t.name for t in tms]

        if args.target:
            if args.target not in names_before:
                sys.exit(f"Target TM '{args.target}' not found. Available: {', '.join(names_before)}")
            target = args.target
        else:
            target = names_before[choose(names_before, "Into which TM should this be merged?")[0]]

        filename = f"{safe_name(target)}.tmx"
        if filename != f"{target}.tmx":
            print(f"  ! TM name contains characters not allowed in file names; using '{filename}'. "
                  "Merge-by-name may not match - check the result.")

        with tempfile.TemporaryDirectory() as tmp:
            parts = write_parts(root, tus, filename, Path(tmp))
            print(f"\nPlan: merge {len(tus)} units into TM '{target}' "
                  f"via {len(parts)} upload(s) named '{filename}'.")
            if args.dry_run:
                print("Dry run - nothing uploaded.")
                return 0
            if not args.yes and input("Proceed? [y/N] ").strip().lower() != "y":
                print("Aborted.")
                return 1

            if not args.no_backup:
                print(f"\nBacking up '{target}' first ...")
                try:
                    bak = download_tm(page, target, folder / "pre_merge_backups",
                                      f"{safe_name(target)}__pre-merge_{timestamp()}.tmx")
                    print(f"  backup saved: {bak}")
                except Exception as exc:  # noqa: BLE001
                    sys.exit(f"Backup failed, aborting (use --no-backup to override): {exc}")

            for n, part in enumerate(parts, 1):
                print(f"\nUploading part {n}/{len(parts)} ...")
                upload_tmx(page, part)

        print("\nVerifying ...")
        page.reload(wait_until="domcontentloaded")
        after = [t.name for t in list_tms(page)]
        new = [n for n in after if n not in names_before]
        if new:
            print(f"  ! A new TM appeared instead of a merge: {', '.join(new)}\n"
                  f"    The file name did not match '{target}'. Fix/merge it manually in the UI.")
            return 2
        print(f"  OK - no new TM was created; entries should now be merged into '{target}'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
