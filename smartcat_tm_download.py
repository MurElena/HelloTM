#!/usr/bin/env python3
"""
Script 1 (Smartcat) - authenticate, choose translation memories, download them as TMX.

    python smartcat_tm_download.py                     # interactive
    python smartcat_tm_download.py --all
    python smartcat_tm_download.py --tm "Marketing TM" # exact name or id (repeatable)
    python smartcat_tm_download.py --server us         # eu (default) | us | ea

Credentials: SMARTCAT_ACCOUNT_ID / SMARTCAT_API_KEY env vars, or you are prompted
(Account ID and API key are under Settings > API in Smartcat; admin rights needed).
Files go to ./tm_backups/smartcat/<TM name>__<timestamp>.tmx (+ manifest.json).
"""
import sys

from smartcat_client import SmartcatProvider
from tm_common import run_download

if __name__ == "__main__":
    sys.exit(run_download(SmartcatProvider(), __doc__))
