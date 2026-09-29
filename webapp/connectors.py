"""Talk to the TMS scripts already in this folder, plus XTM and the demo set."""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tm_common import ProviderError, safe_name, timestamp  # noqa: E402

from webapp.demo import list_tms as demo_tms  # noqa: E402
from webapp.xtm_client import XtmProvider  # noqa: E402

DATA = ROOT / "data"
EXPORTS = DATA / "exports"


def _phrase(creds: dict):
    os.environ["TM_TOOLS_HOME"] = str(DATA / "tm_tools")
    if creds.get("token"):
        os.environ["PHRASE_TOKEN"] = creds["token"]
    else:
        os.environ.pop("PHRASE_TOKEN", None)
    if creds.get("username"):
        os.environ["PHRASE_USERNAME"] = creds["username"]
    if creds.get("password"):
        os.environ["PHRASE_PASSWORD"] = creds["password"]
    from phrase_client import PhraseProvider
    provider = PhraseProvider()
    args = SimpleNamespace(
        region=creds.get("region") or "eu",
        base_url=creds.get("base_url") or None,
        otp=creds.get("otp") or None,
        relogin=bool(creds.get("relogin")),
        strict_lang=False,
    )
    provider.authenticate(args)
    return provider


def _smartcat(creds: dict):
    if creds.get("account_id"):
        os.environ["SMARTCAT_ACCOUNT_ID"] = creds["account_id"]
    if creds.get("api_key"):
        os.environ["SMARTCAT_API_KEY"] = creds["api_key"]
    from smartcat_client import SmartcatProvider
    provider = SmartcatProvider()
    args = SimpleNamespace(
        server=creds.get("server") or "eu",
        no_tags=False,
        replace_all_content=False,
    )
    provider.authenticate(args)
    return provider


def _xtm(creds: dict) -> XtmProvider:
    provider = XtmProvider()
    provider.authenticate(creds)
    return provider


def test_connection(provider: str, creds: dict) -> str:
    if provider == "demo":
        return "Demo workspace"
    if provider == "local":
        return "Local TMX files"
    if provider == "phrase":
        _phrase(creds)
        return "Phrase TMS"
    if provider == "smartcat":
        _smartcat(creds)
        return "Smartcat"
    if provider == "xtm":
        _xtm(creds)
        return creds.get("client") or "XTM"
    if provider == "lokalise":
        return _lokalise_status(creds)
    raise ProviderError(f"Unknown provider {provider}")


def list_remote_tms(provider: str, creds: dict) -> list[dict]:
    if provider == "demo":
        return demo_tms()
    if provider == "local":
        return []
    if provider == "phrase":
        tms = _phrase(creds).list_tms()
    elif provider == "smartcat":
        tms = _smartcat(creds).list_tms()
    elif provider == "xtm":
        tms = _xtm(creds).list_tms()
    elif provider == "lokalise":
        return _lokalise_list()
    else:
        raise ProviderError(f"Unknown provider {provider}")
    return [
        {
            "id": tm.id,
            "name": tm.name,
            "source": tm.source,
            "targets": list(tm.targets),
            "units": None,
        }
        for tm in tms
    ]


def export_remote(provider: str, creds: dict, tm: dict) -> Path:
    EXPORTS.mkdir(parents=True, exist_ok=True)
    dest = EXPORTS / f"{safe_name(tm['name'])}__{timestamp()}.tmx"
    from tm_common import TMInfo
    info = TMInfo(str(tm["id"]), tm["name"], tm.get("source") or "", list(tm.get("targets") or []))
    if provider == "phrase":
        return _phrase(creds).export_tm(info, dest)
    if provider == "smartcat":
        return _smartcat(creds).export_tm(info, dest)
    if provider == "xtm":
        return _xtm(creds).export_tm(info, dest)
    if provider == "lokalise":
        return _lokalise_download(tm["name"], dest)
    raise ProviderError(f"Cannot export from {provider}")


def _lokalise_status(creds: dict) -> str:
    home = DATA / "lokalise"
    os.environ["LOKALISE_TM_HOME"] = str(home)
    if creds.get("email"):
        os.environ["LOKALISE_EMAIL"] = creds["email"]
    if creds.get("password"):
        os.environ["LOKALISE_PASSWORD"] = creds["password"]
    if (home / "browser_profile").exists() and (home / "config.json").exists():
        return "Lokalise session saved"
    return "Lokalise credentials saved. Launch the browser login to list TMs."


def _lokalise_list() -> list[dict]:
    os.environ["LOKALISE_TM_HOME"] = str(DATA / "lokalise")
    import builtins
    from lokalise_ui import list_tms, lokalise_session

    def _blocked(prompt: str = ""):
        raise RuntimeError(
            "Lokalise is not logged in. Use Launch browser login, open Team settings > "
            "Translation memory, then click Continue."
        )

    original = builtins.input
    builtins.input = _blocked
    try:
        with lokalise_session(headed=False, reset=False) as page:
            found = list_tms(page)
    finally:
        builtins.input = original
    return [{"id": tm.name, "name": tm.name, "source": "", "targets": [], "units": None} for tm in found]


def _lokalise_download(name: str, dest: Path) -> Path:
    os.environ["LOKALISE_TM_HOME"] = str(DATA / "lokalise")
    from lokalise_ui import download_tm, lokalise_session
    with lokalise_session(headed=False, reset=False) as page:
        return download_tm(page, name, dest.parent, dest.name)


def list_glossaries(provider: str, creds: dict) -> tuple[list[dict], str]:
    """Return glossaries found on the TMS, plus a short error when the list fails."""
    if provider == "demo":
        from webapp.store import list_demo_glossaries
        return list_demo_glossaries(), ""
    if provider in {"local", "lokalise"}:
        note = ""
        if provider == "lokalise":
            note = "Lokalise has no glossary list in this connection. Upload an .xlsx glossary instead."
        return [], note
    try:
        if provider == "phrase":
            return _phrase_glossaries(creds), ""
        if provider == "smartcat":
            return _smartcat_glossaries(creds), ""
        if provider == "xtm":
            return _xtm_glossaries(creds), ""
    except Exception as exc:  # noqa: BLE001
        return [], str(exc)
    return [], f"No glossary list for {provider}."


def export_glossary(provider: str, creds: dict, glossary_id: str) -> Path:
    folder = DATA / "glossaries"
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / f"{safe_name(glossary_id)}.xlsx"
    if provider == "demo":
        from webapp.store import demo_glossary_path
        src = demo_glossary_path(glossary_id)
        shutil.copy(src, dest)
        return dest
    if provider == "phrase":
        return _phrase_export_glossary(creds, glossary_id, dest)
    if provider == "smartcat":
        return _smartcat_export_glossary(creds, glossary_id, dest)
    if provider == "xtm":
        return _xtm_export_glossary(creds, glossary_id, dest)
    raise ProviderError(f"Cannot download a glossary from {provider}.")


def import_remote(provider: str, creds: dict, tm: dict, path: Path) -> str:
    from tm_common import TMInfo
    info = TMInfo(str(tm["id"]), tm.get("name") or str(tm["id"]), tm.get("source") or "", list(tm.get("targets") or []))
    args = SimpleNamespace(strict_lang=False, replace_all_content=True, no_tags=False)
    if provider == "phrase":
        note = _phrase(creds).import_tm(info, path, args)
        return f"{note}. Phrase merges this file in, so segments removed here can remain in the remote memory."
    if provider == "smartcat":
        note = _smartcat(creds).import_tm(info, path, args)
        return f"{note}. Smartcat replaced the memory with this cleaned file."
    if provider == "xtm":
        return _xtm(creds).import_cleaned(info, path)
    if provider == "lokalise":
        return _lokalise_upload(tm.get("name") or path.stem, path)
    if provider in {"demo", "local"}:
        dest_dir = DATA / "commits"
        dest_dir.mkdir(parents=True, exist_ok=True)
        target = dest_dir / f"{safe_name(tm.get('name') or 'memory')}.tmx"
        shutil.copy(path, target)
        return f"Saved {target.name} on this machine. This workspace has no remote TMS."
    raise ProviderError(f"Cannot upload a memory to {provider}.")


def _as_glossary(item: dict) -> dict | None:
    gid = item.get("uid") or item.get("id") or item.get("glossaryId")
    if gid is None:
        return None
    langs = item.get("langs") or item.get("languages") or item.get("languageCodes") or []
    if langs and isinstance(langs[0], dict):
        langs = [x.get("code") or x.get("lang") or x.get("language") or "" for x in langs]
    entries = item.get("termCount") or item.get("termsCount") or item.get("entries")
    return {
        "id": str(gid),
        "name": item.get("name") or item.get("title") or "Glossary",
        "languages": [str(lang) for lang in langs if lang],
        "entries": entries,
    }


def _phrase_glossaries(creds: dict) -> list[dict]:
    provider = _phrase(creds)
    last = None
    data = None
    for path in ("/api2/v1/termBases", "/api2/v2/termBases"):
        try:
            data = provider._call("GET", path, params={"pageNumber": 0, "pageSize": 50}).json()
            break
        except ProviderError as exc:
            last = exc
    if data is None:
        raise last or ProviderError("Phrase did not list term bases.")
    content = data.get("content") if isinstance(data, dict) else data
    return [row for row in (_as_glossary(item) for item in content or [] if isinstance(item, dict)) if row]


def _phrase_export_glossary(creds: dict, glossary_id: str, dest: Path) -> Path:
    provider = _phrase(creds)
    response = provider._call(
        "GET", f"/api2/v1/termBases/{glossary_id}/export", params={"format": "XLSX"}, stream=True
    )
    kind = (response.headers.get("Content-Type") or "").lower()
    if "json" in kind:
        raise ProviderError(f"Phrase glossary export did not return a workbook: {response.text[:240]}")
    from tm_common import stream_to_file
    return stream_to_file(response, dest)


def _smartcat_glossaries(creds: dict) -> list[dict]:
    provider = _smartcat(creds)
    last = None
    data = None
    for path in ("/api/integration/v1/glossaries", "/api/integration/v1/glossary"):
        try:
            data = provider._call("GET", path).json()
            break
        except ProviderError as exc:
            last = exc
    if data is None:
        raise last or ProviderError("Smartcat did not list glossaries.")
    if isinstance(data, dict):
        data = data.get("glossaries") or data.get("items") or data.get("content") or []
    return [row for row in (_as_glossary(item) for item in data or [] if isinstance(item, dict)) if row]


def _smartcat_export_glossary(creds: dict, glossary_id: str, dest: Path) -> Path:
    provider = _smartcat(creds)
    last = None
    for path in (
        f"/api/integration/v1/glossary/{glossary_id}/export",
        f"/api/integration/v1/glossaries/{glossary_id}/export",
    ):
        try:
            response = provider._call("GET", path, params={"format": "xlsx"}, stream=True)
        except ProviderError as exc:
            last = exc
            continue
        kind = (response.headers.get("Content-Type") or "").lower()
        if "json" in kind:
            last = ProviderError(response.text[:240])
            continue
        from tm_common import stream_to_file
        return stream_to_file(response, dest)
    raise last or ProviderError("Smartcat did not export that glossary.")


def _xtm_glossaries(creds: dict) -> list[dict]:
    provider = _xtm(creds)
    last = None
    data = None
    for path in ("/term-bases", "/terminology", "/customers"):
        try:
            data = provider.s.get(provider.base + path, timeout=60)
            if data.status_code >= 400:
                last = ProviderError(f"XTM {path} returned {data.status_code}")
                data = None
                continue
            data = data.json()
            if path != "/customers":
                break
            data = None
            last = ProviderError("XTM did not expose a term-base list on this account.")
        except Exception as exc:  # noqa: BLE001
            last = ProviderError(str(exc))
            data = None
    if not isinstance(data, (list, dict)):
        raise last or ProviderError("XTM did not list glossaries.")
    if isinstance(data, dict):
        data = data.get("termBases") or data.get("items") or data.get("content") or []
    found = [row for row in (_as_glossary(item) for item in data or [] if isinstance(item, dict)) if row]
    if not found:
        raise last or ProviderError("XTM did not expose a term-base list on this account.")
    return found


def _xtm_export_glossary(creds: dict, glossary_id: str, dest: Path) -> Path:
    raise ProviderError("XTM glossary download is not available from this connection. Upload an .xlsx file instead.")


def _lokalise_upload(name: str, path: Path) -> str:
    os.environ["LOKALISE_TM_HOME"] = str(DATA / "lokalise")
    from lokalise_ui import lokalise_session, upload_tmx
    staged_dir = DATA / "commits"
    staged_dir.mkdir(parents=True, exist_ok=True)
    staged = staged_dir / f"{name}.tmx"
    shutil.copy(path, staged)
    with lokalise_session(headed=False, reset=False) as page:
        upload_tmx(page, staged)
    return f"Uploaded {staged.name}. Lokalise merges a file when its name matches the memory."
