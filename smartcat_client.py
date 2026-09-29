"""
Smartcat provider.

Endpoints used (Smartcat Integration API, https://developers.smartcat.com):
  GET  /api/integration/v1/account                     -> verify credentials
  GET  /api/integration/v1/translationmemory           -> list TMs (lastProcessedId / batchSize paging)
  GET  /api/integration/v1/translationmemory/{id}/file -> export TMX (withTags)
  POST /api/integration/v1/translationmemory/{id}      -> import TMX (multipart; replaceAllContent flag)
  GET  /api/integration/v1/translationmemory/task      -> pending TMX import tasks

Auth is HTTP Basic: username = Account ID, password = an API key (Settings > API in Smartcat).

IMPORTANT: 'replaceAllContent=false' (the default here) ADDS the file's entries to the TM.
'replaceAllContent=true' wipes the TM and replaces it with the file. Replace mode is only
available with --replace-all-content and needs you to type the TM name to confirm.
"""
from __future__ import annotations

import getpass
import os
import time
from pathlib import Path

import requests

from tm_common import ProviderError, TMInfo, api_call, stream_to_file

SERVERS = {
    "eu": "https://smartcat.ai",
    "us": "https://us.smartcat.ai",
    "ea": "https://ea.smartcat.ai",
}
API = "/api/integration/v1"
BATCH = 100
TMX_FIELD = "tmxFile"  # multipart field name of the import endpoint; change here if Smartcat differs
DONE_STATES = {"completed", "complete", "done", "success", "succeeded", "finished"}
FAILED_STATES = {"failed", "error", "cancelled", "canceled"}


class SmartcatProvider:
    name = "smartcat"

    def __init__(self) -> None:
        self.s = requests.Session()
        self.s.headers["User-Agent"] = "tm-api-tools/1.0"
        self.base = SERVERS["eu"]

    # ---- CLI ------------------------------------------------------------- #
    def add_args(self, ap) -> None:
        ap.add_argument("--server", default=os.environ.get("SMARTCAT_SERVER", "eu"),
                        help="eu | us | ea, or a full URL (env SMARTCAT_SERVER). Default: eu")
        ap.add_argument("--no-tags", action="store_true", help="export: leave inline tags out of the TMX")
        ap.add_argument("--replace-all-content", action="store_true",
                        help="DANGEROUS: import replaces the TM's entire content instead of adding to it")

    def describe_mode(self, args) -> str:
        if args.replace_all_content:
            return "REPLACE ALL CONTENT of the TM with the file (existing entries are removed!)"
        return "MERGE: add the file's entries to the existing TM (replaceAllContent=false)"

    def confirm_extra(self, args, tm) -> bool:
        if not args.replace_all_content:
            return True
        typed = input(f"Replace mode will erase the current content of '{tm.name}'. "
                      "Type the TM name to confirm: ").strip()
        return typed == tm.name

    # ---- HTTP ------------------------------------------------------------ #
    def _call(self, method: str, path: str, **kw):
        return api_call(self.s, method, self.base + path, **kw)

    # ---- Authentication -------------------------------------------------- #
    def authenticate(self, args) -> None:
        self.base = (SERVERS.get(args.server) or args.server).rstrip("/")
        self._no_tags = args.no_tags
        account_id = os.environ.get("SMARTCAT_ACCOUNT_ID") or input("Smartcat Account ID: ").strip()
        api_key = os.environ.get("SMARTCAT_API_KEY") or getpass.getpass("Smartcat API key: ")
        self.s.auth = (account_id, api_key)
        try:
            info = self._call("GET", f"{API}/account").json()
        except ProviderError as exc:
            hint = ""
            if exc.status in (401, 403):
                hint = "\n  Check Account ID / API key (Settings > API) and that --server matches your region."
            raise ProviderError(f"Authentication failed: {exc}{hint}", exc.status) from None
        print(f"Authenticated to Smartcat account '{info.get('name') or account_id}'.")

    # ---- Listing --------------------------------------------------------- #
    def list_tms(self) -> list[TMInfo]:
        out, last = [], ""
        while True:
            batch = self._call("GET", f"{API}/translationmemory",
                               params={"lastProcessedId": last, "batchSize": BATCH}).json()
            if not batch:
                break
            for t in batch:
                out.append(TMInfo(str(t["id"]), t.get("name") or str(t["id"]),
                                  t.get("sourceLanguage") or "", t.get("targetLanguages") or []))
            new_last = str(batch[-1]["id"])
            if len(batch) < BATCH or new_last == last:
                break
            last = new_last
        return out

    # ---- Export ---------------------------------------------------------- #
    def export_tm(self, tm: TMInfo, dest: Path) -> Path:
        r = self._call("GET", f"{API}/translationmemory/{tm.id}/file",
                       params={"withTags": "false" if getattr(self, "_no_tags", False) else "true"},
                       stream=True)
        if "json" in (r.headers.get("Content-Type") or "").lower():
            raise ProviderError(f"Export did not return a TMX file: {r.text[:300]}")
        return stream_to_file(r, dest)

    # ---- Import ---------------------------------------------------------- #
    def import_tm(self, tm: TMInfo, tmx: Path, args) -> str:
        flag = str(bool(args.replace_all_content)).lower()
        with open(tmx, "rb") as fh:
            r = self._call("POST", f"{API}/translationmemory/{tm.id}",
                           params={"replaceAllContent": flag},
                           data={"replaceAllContent": flag},
                           files={TMX_FIELD: (tmx.name, fh, "application/xml")})
        task_id = self._task_id(r)
        return self._wait_import(task_id)

    @staticmethod
    def _task_id(r) -> str | None:
        try:
            body = r.json()
        except ValueError:
            return None
        if isinstance(body, str):
            return body
        if isinstance(body, list) and body:
            body = body[0]
        if isinstance(body, dict):
            for k in ("id", "taskId"):
                if body.get(k):
                    return str(body[k])
        return None

    def _wait_import(self, task_id: str | None, timeout: int = 1800) -> str:
        deadline, delay = time.time() + timeout, 2.0
        while time.time() < deadline:
            tasks = self._call("GET", f"{API}/translationmemory/task").json()
            if isinstance(tasks, dict):
                tasks = tasks.get("items") or tasks.get("tasks") or []
            ours = [t for t in tasks if not task_id or str(t.get("id")) == task_id]
            if not ours:
                return "import task finished (no longer pending)"
            states = {str(t.get("status", "")).lower() for t in ours}
            if states & FAILED_STATES:
                raise ProviderError(f"Import task failed: {ours}")
            if states and states <= DONE_STATES:
                return f"import task finished ({', '.join(sorted(states))})"
            time.sleep(delay)
            delay = min(delay * 1.5, 15)
        return ("import task still listed as pending after "
                f"{timeout // 60} min - check the TM in the Smartcat UI")
