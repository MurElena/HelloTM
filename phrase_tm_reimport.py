#!/usr/bin/env python3
"""
Script 2 (Phrase TMS) - pick a local TMX file and a target TM, then import (merge) it.

    python phrase_tm_reimport.py                                   # interactive
    python phrase_tm_reimport.py --file X.tmx --target "Marketing TM" --dry-run

Steps: validate TMX -> pick target TM -> language check -> confirm -> back up target TM
-> import via the async import endpoint -> wait for the job and report warnings/errors.
The import adds the file to the EXISTING TM (it does not create a new one).
"""
import sys

from phrase_client import PhraseProvider
from tm_common import run_reimport

if __name__ == "__main__":
    sys.exit(run_reimport(PhraseProvider(), __doc__))
