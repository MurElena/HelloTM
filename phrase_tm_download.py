#!/usr/bin/env python3
"""
Script 1 (Phrase TMS) - authenticate, choose translation memories, download them as TMX.

    python phrase_tm_download.py                      # interactive
    python phrase_tm_download.py --all
    python phrase_tm_download.py --tm "Marketing TM"  # exact name or uid (repeatable)
    python phrase_tm_download.py --region us          # US data center

Credentials: PHRASE_USERNAME / PHRASE_PASSWORD env vars, or you are prompted (password hidden).
The API token (valid 24 h) is cached in ./.tm_tools/ so later runs do not log in again.
Org enforces SSO for API tokens?  Put a token in PHRASE_TOKEN instead.
Files go to ./tm_backups/phrase/<TM name>__<timestamp>.tmx (+ manifest.json).
"""
import sys

from phrase_client import PhraseProvider
from tm_common import run_download

if __name__ == "__main__":
    sys.exit(run_download(PhraseProvider(), __doc__))
