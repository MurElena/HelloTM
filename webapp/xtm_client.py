"""XTM Cloud provider.

The app folder ships Phrase, Smartcat, and Lokalise clients. XTM is reached
through the same shape of client, using the XTM Cloud REST API:

  POST /rest-api/auth/token
  GET  /rest-api/customers
  GET  /rest-api/customers/{id}/language-combinations
  POST /rest-api/translation-memory/files/generate
  GET  /rest-api/translation-memory/files/{fileId}/status
  GET  /rest-api/translation-memory/files/{fileId}/download
"""
from __future__ import annotations

import time
from pathlib import Path

import requests

from tm_common import ProviderError, TMInfo, api_call, stream_to_file


class XtmProvider:
    name = "xtm"

    def __init__(self) -> None:
        self.s = requests.Session()
        self.s.headers["User-Agent"] = "tm-cleaner/1.0"
        self.base = "https://xtm.cloud/rest-api"

    def authenticate(self, creds: dict) -> str:
        raw = (creds.get("base_url") or "https://xtm.cloud").strip().rstrip("/")
        if raw.endswith("/rest-api"):
            self.base = raw
        else:
            self.base = raw + "/rest-api"
        user_id = creds.get("user_id")
        try:
            user_id = int(user_id)
        except (TypeError, ValueError) as exc:
            raise ProviderError("XTM user ID must be a number.") from exc
        body = {
            "client": (creds.get("client") or "").strip(),
            "password": creds.get("password") or "",
            "userId": user_id,
        }
        if not body["client"] or not body["password"]:
            raise ProviderError("XTM needs a client name, user ID, and password.")
        try:
            data = api_call(self.s, "POST", self.base + "/auth/token", json=body).json()
        except ProviderError as exc:
            raise ProviderError(f"XTM login failed: {exc}", exc.status) from None
        token = data.get("token")
        if not token:
            raise ProviderError("XTM did not return a token.")
        self.s.headers["Authorization"] = f"XTM-Basic {token}"
        return token

    def list_tms(self) -> list[TMInfo]:
        customers = api_call(self.s, "GET", self.base + "/customers").json()
        if isinstance(customers, dict):
            customers = customers.get("customers") or customers.get("items") or []
        out: list[TMInfo] = []
        for customer in customers:
            if not isinstance(customer, dict):
                continue
            if str(customer.get("activity") or "ACTIVE").upper() == "INACTIVE":
                continue
            cid = customer.get("id")
            name = customer.get("name") or f"Customer {cid}"
            pairs = self._pairs(cid)
            if not pairs:
                out.append(TMInfo(f"{cid}", name, "", []))
                continue
            for src, tgt in pairs:
                out.append(TMInfo(
                    f"{cid}|{src}|{tgt}",
                    f"{name}  {src} → {tgt}",
                    src,
                    [tgt],
                ))
        return out

    def _pairs(self, customer_id) -> list[tuple[str, str]]:
        try:
            data = api_call(
                self.s, "GET", f"{self.base}/customers/{customer_id}/language-combinations"
            ).json()
        except ProviderError:
            return []
        pairs = data.get("languagePairs") if isinstance(data, dict) else None
        found = []
        for pair in pairs or []:
            src = pair.get("sourceLanguageCode") or ""
            tgt = pair.get("targetLanguageCode") or ""
            if src and tgt:
                found.append((src, tgt))
        return found

    def export_tm(self, tm: TMInfo, dest: Path) -> Path:
        parts = str(tm.id).split("|")
        body: dict = {"fileType": "TMX", "customerId": int(parts[0])}
        if len(parts) >= 3:
            body["sourceLanguage"] = parts[1]
            body["targetLanguage"] = parts[2]
        created = api_call(
            self.s, "POST", self.base + "/translation-memory/files/generate", json=body
        ).json()
        file_id = created.get("fileId")
        if not file_id:
            raise ProviderError(f"XTM did not start a TM export: {created}")
        self._wait(file_id)
        response = api_call(
            self.s, "GET", f"{self.base}/translation-memory/files/{file_id}/download", stream=True
        )
        return stream_to_file(response, dest)

    def _wait(self, file_id, timeout: int = 1800) -> None:
        deadline = time.time() + timeout
        delay = 2.0
        while time.time() < deadline:
            data = api_call(
                self.s, "GET", f"{self.base}/translation-memory/files/{file_id}/status"
            ).json()
            status = str(data.get("status") or "").upper()
            if status == "FINISHED":
                return
            if status == "ERROR":
                raise ProviderError(data.get("message") or "XTM TM export failed.")
            time.sleep(delay)
            delay = min(delay * 1.5, 15)
        raise ProviderError("XTM TM export did not finish in time.")

    def import_cleaned(self, tm: TMInfo, path: Path) -> str:
        parts = str(tm.id).split("|")
        with open(path, "rb") as fh:
            api_call(
                self.s,
                "POST",
                self.base + "/translation-memory/files/import",
                files={"file": (path.name, fh, "application/xml")},
                data={"customerId": parts[0], "fileType": "TMX"},
            )
        return "XTM accepted the cleaned TMX."
