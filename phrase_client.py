"""
Phrase TMS (formerly Memsource) provider.

Endpoints used (Phrase TMS API, https://developers.phrase.com):
  POST /api2/v3/auth/login                         -> API token (valid 24 h)
  GET  /api2/v2/transMemories                      -> list TMs (paged)
  POST /api2/v2/transMemories/{uid}/export         -> async export job  (fallback: v1 exportByQueryAsync)
  GET  /api2/v1/async/{id}                         -> poll job; asyncResponse stays null until finished
  GET  /api2/v1/transMemories/downloadExport/{id}  -> download the finished TMX
  POST /api2/v2/transMemories/{uid}/import         -> async import of TMX into an EXISTING TM

Note: this is Phrase TMS. Phrase Strings (the developer localization product) has no TMs.
"""
from __future__ import annotations

import getpass
import hashlib
import json
import os
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

import requests

from tm_common import ProviderError, TMInfo, api_call, stream_to_file

HOME = Path(os.environ.get("TM_TOOLS_HOME", ".tm_tools"))
REGIONS = {
    "eu": "https://cloud.memsource.com/web",
    "us": "https://us.cloud.memsource.com/web",
}


class PhraseProvider:
    name = "phrase"

    def __init__(self) -> None:
        self.s = requests.Session()
        self.s.headers["User-Agent"] = "tm-api-tools/1.0"
        self.base = REGIONS["eu"]

    # ---- CLI ------------------------------------------------------------- #
    def add_args(self, ap) -> None:
        ap.add_argument("--region", default=os.environ.get("PHRASE_REGION", "eu"),
                        choices=sorted(REGIONS), help="Phrase data center (env PHRASE_REGION)")
        ap.add_argument("--base-url", default=os.environ.get("PHRASE_BASE_URL"),
                        help="override API base URL, e.g. https://cloud.memsource.com/web")
        ap.add_argument("--otp", help="2FA code, if your account uses two-factor login")
        ap.add_argument("--relogin", action="store_true", help="ignore the cached API token")
        ap.add_argument("--strict-lang", action="store_true",
                        help="import: require TMX language codes to match the TM exactly")

    def describe_mode(self, args) -> str:
        return "IMPORT into the existing TM (Phrase adds the file's segments to it)"

    def confirm_extra(self, args, tm) -> bool:
        return True

    # ---- HTTP ------------------------------------------------------------ #
    def _call(self, method: str, path: str, **kw):
        return api_call(self.s, method, self.base + path, **kw)

    # ---- Authentication -------------------------------------------------- #
    def authenticate(self, args) -> None:
        self.base = (args.base_url or REGIONS[args.region]).rstrip("/")

        token = os.environ.get("PHRASE_TOKEN")  # e.g. for SSO orgs / CI
        if token:
            self._set_token(token)
            self._verify()
            print("Authenticated with PHRASE_TOKEN.")
            return

        username = os.environ.get("PHRASE_USERNAME") or input("Phrase username: ").strip()
        cache = HOME / f"phrase_{hashlib.sha256((self.base + username).encode()).hexdigest()[:12]}.json"

        if not args.relogin and cache.exists():
            try:
                c = json.loads(cache.read_text())
                if datetime.fromisoformat(c["expires"]) > datetime.now(timezone.utc):
                    self._set_token(c["token"])
                    self._verify()
                    print(f"Authenticated as {username} (cached token).")
                    return
            except (OSError, ValueError, KeyError, ProviderError):
                pass

        password = os.environ.get("PHRASE_PASSWORD") or getpass.getpass("Phrase password: ")
        body = {"userName": username, "password": password}
        if args.otp:
            body["code"] = args.otp
        try:
            data = self._call("POST", "/api2/v3/auth/login", json=body).json()
        except ProviderError as exc:
            hint = ""
            if exc.status in (401, 403):
                hint = ("\n  Check username/password, add --otp for 2FA, or - if your org enforces SSO for "
                        "API tokens - create a token another way and pass it via PHRASE_TOKEN.")
            raise ProviderError(f"Login failed: {exc}{hint}", exc.status) from None
        self._set_token(data["token"])
        HOME.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"token": data["token"], "expires": data.get("expires") or
                                     "1970-01-01T00:00:00+00:00"}))
        try:
            cache.chmod(0o600)
        except OSError:
            pass
        who = (data.get("user") or {}).get("userName", username)
        print(f"Authenticated as {who}.")

    def _set_token(self, token: str) -> None:
        self.s.headers["Authorization"] = f"ApiToken {token}"

    def _verify(self) -> None:
        self._call("GET", "/api2/v2/transMemories", params={"pageSize": 1})

    # ---- Listing --------------------------------------------------------- #
    def list_tms(self) -> list[TMInfo]:
        out, page = [], 0
        while True:
            d = self._call("GET", "/api2/v2/transMemories",
                           params={"pageNumber": page, "pageSize": 100, "sortField": "NAME"}).json()
            for t in d.get("content", []):
                out.append(TMInfo(t["uid"], t.get("name") or t["uid"], t.get("sourceLang") or "",
                                  t.get("targetLangs") or []))
            page += 1
            if page >= (d.get("totalPages") or 1):
                return out

    # ---- Async jobs ------------------------------------------------------ #
    def _wait(self, async_id: str, label: str, timeout: int = 3600) -> dict:
        deadline, delay = time.time() + timeout, 2.0
        while time.time() < deadline:
            d = self._call("GET", f"/api2/v1/async/{async_id}").json()
            resp = d.get("asyncResponse")
            if resp:
                if resp.get("errorCode") or resp.get("errorDesc"):
                    details = "; ".join(x.get("message") or x.get("code", "") for x in resp.get("errorDetails") or [])
                    raise ProviderError(f"{label} failed: {resp.get('errorCode')} {resp.get('errorDesc')} {details}".strip())
                for w in resp.get("warnings") or []:
                    print(f"  ! warning: {w.get('message') or w.get('code')}")
                return resp
            time.sleep(delay)
            delay = min(delay * 1.5, 15)
        raise ProviderError(f"{label} did not finish within {timeout // 60} minutes (async id {async_id})")

    # ---- Export ---------------------------------------------------------- #
    def _start_export(self, uid: str) -> str:
        try:
            r = self._call("POST", f"/api2/v2/transMemories/{uid}/export", json={})
        except ProviderError as exc:
            if exc.status not in (404, 405, 410):
                raise
            r = self._call("POST", f"/api2/v1/transMemories/{uid}/exportByQueryAsync", json={})
        return r.json()["asyncRequest"]["id"]

    def export_tm(self, tm: TMInfo, dest: Path) -> Path:
        async_id = self._start_export(tm.id)
        print("  export queued, waiting for Phrase ...")
        self._wait(async_id, "Export")
        r = self._call("GET", f"/api2/v1/transMemories/downloadExport/{async_id}",
                       params={"format": "TMX"}, stream=True)
        return stream_to_file(r, dest)

    # ---- Import ---------------------------------------------------------- #
    def import_tm(self, tm: TMInfo, tmx: Path, args) -> str:
        headers = {
            "Content-Type": "application/octet-stream",
            "Content-Disposition": f"attachment; filename*=UTF-8''{urllib.parse.quote(tmx.name)}",
        }
        with open(tmx, "rb") as fh:
            r = self._call("POST", f"/api2/v2/transMemories/{tm.id}/import",
                           params={"strictLangMatching": str(bool(args.strict_lang)).lower()},
                           data=fh, headers=headers)
        async_id = r.json()["asyncRequest"]["id"]
        resp = self._wait(async_id, "Import")
        extra = f" (accepted segments: {resp['acceptedSegmentsCount']})" if resp.get("acceptedSegmentsCount") is not None else ""
        return f"import finished{extra}"
