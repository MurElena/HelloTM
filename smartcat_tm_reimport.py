#!/usr/bin/env python3
"""
Script 2 (Smartcat) - pick a local TMX file and a target TM, then import it.

    python smartcat_tm_reimport.py                                   # interactive
    python smartcat_tm_reimport.py --file X.tmx --target "Marketing TM" --dry-run

Default mode MERGES: the file entries are added to the existing TM (replaceAllContent=false).
--replace-all-content wipes the TM and replaces it with the file; you must type the TM name.
A backup of the target TM is downloaded first unless you pass --no-backup.
"""
import sys

from smartcat_client import SmartcatProvider
from tm_common import run_reimport

if __name__ == "__main__":
    sys.exit(run_reimport(SmartcatProvider(), __doc__))
