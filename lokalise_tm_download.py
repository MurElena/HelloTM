#!/usr/bin/env python3
"""
Script 1 - authenticate into Lokalise, choose translation memories, download them as TMX.

    python tm_download.py                    # interactive
    python tm_download.py --all              # download every TM
    python tm_download.py --tm "Marketing"   # non-interactive, by exact name (repeatable)

First run: a browser window opens. Log in (SSO/2FA are fine), then open
Team settings > Translation memory and press Enter. The session and page URL are
remembered, so later runs are headless and need no login until the session expires.

Files are saved to ./tm_backups/<TM name>__<timestamp>.tmx and recorded in
./tm_backups/manifest.json (used by tm_reimport.py).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from lokalise_ui import (add_common_args, choose, download_tm, list_tms,
                         lokalise_session, safe_name, timestamp)


def update_manifest(out: Path, entry: dict) -> None:
    mf = out / "manifest.json"
    try:
        data = json.loads(mf.read_text())
    except (OSError, ValueError):
        data = []
    data.append(entry)
    mf.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    add_common_args(ap)
    ap.add_argument("--out", default="tm_backups", help="output folder (default: tm_backups)")
    ap.add_argument("--all", action="store_true", help="download all translation memories")
    ap.add_argument("--tm", action="append", metavar="NAME",
                    help="download the TM with this exact name (repeatable)")
    args = ap.parse_args()
    out = Path(args.out)

    with lokalise_session(headed=args.headed, reset=args.reset) as page:
        tms = list_tms(page)
        print(f"Found {len(tms)} translation memory storage(s).")

        if args.all:
            chosen = tms
        elif args.tm:
            wanted = set(args.tm)
            chosen = [t for t in tms if t.name in wanted]
            missing = wanted - {t.name for t in chosen}
            if missing:
                print(f"Not found: {', '.join(sorted(missing))}", file=sys.stderr)
        else:
            picks = choose([t.name for t in tms], "Which TM(s) do you want to download?", multi=True)
            chosen = [tms[i] for i in picks]

        if not chosen:
            print("Nothing to download.")
            return 1

        failures = 0
        for tm in chosen:
            print(f"\nDownloading '{tm.name}' ...")
            filename = f"{safe_name(tm.name)}__{timestamp()}.tmx"
            try:
                path = download_tm(page, tm.name, out, filename)
            except Exception as exc:  # noqa: BLE001 - keep going with the other TMs
                failures += 1
                print(f"  FAILED: {exc}", file=sys.stderr)
                continue
            size_mb = path.stat().st_size / 1_048_576
            print(f"  saved {path}  ({size_mb:.1f} MB)")
            update_manifest(out, {"tm_name": tm.name, "file": path.name, "downloaded_at": timestamp()})

    print(f"\nDone. {len(chosen) - failures}/{len(chosen)} downloaded to {out.resolve()}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
