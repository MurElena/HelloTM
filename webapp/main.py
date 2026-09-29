"""TM Cleaner web app."""
from __future__ import annotations

import json
import os
import sys
import threading
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from tm_common import ProviderError

from webapp import store
from webapp.checks import BUILTIN_CHECKS, check_by_id, default_prompt
from webapp.connectors import export_glossary, export_remote, import_remote, list_glossaries, list_remote_tms, test_connection
from webapp.demo import clone_tus
from webapp.engine import XML_LANG, parse_tmx, run_corpus

STATIC = ROOT / "static"
UPLOADS = store.DATA / "uploads"
GLOSSARY_DIR = store.DATA / "glossary"

app = FastAPI(title="TM Cleaner")
app.mount("/assets", StaticFiles(directory=STATIC), name="assets")

_LOGIN = {"waiting": False, "prompt": "", "error": "", "done": False, "busy": False}
_LOGIN_EVENT = threading.Event()
_RUN_LOCK = threading.Lock()


@app.on_event("startup")
def _startup() -> None:
    store.ensure_seed()


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


def _checks_payload() -> list[dict]:
    data = store.load()
    custom = []
    for item in data["customChecks"]:
        custom.append({
            "id": item["id"],
            "name": item["name"],
            "detail": item.get("detail") or "Custom prompt",
            "kind": "ai",
            "action": "flag",
            "group": "AI",
            "custom": True,
            "prompt": item.get("prompt") or "",
        })
    out = []
    for item in BUILTIN_CHECKS:
        row = dict(item)
        if row["id"] == "llm_severity":
            row["prompt"] = default_prompt()
        out.append(row)
    out.extend(custom)
    return out


@app.get("/api/checks")
def api_checks():
    return {"checks": _checks_payload(), "promptTemplate": default_prompt()}


@app.post("/api/checks")
def api_add_check(body: dict):
    name = (body.get("name") or "").strip()
    prompt = (body.get("prompt") or "").strip()
    if not name or not prompt:
        raise HTTPException(400, "A name and a prompt are required.")
    if "{source}" not in prompt or "{target}" not in prompt:
        raise HTTPException(400, "The prompt must include {source} and {target}.")
    item = {
        "id": store.new_id("llm"),
        "name": name,
        "detail": (body.get("detail") or "Custom AI check").strip(),
        "prompt": prompt,
    }

    def mutate(data):
        data["customChecks"].append(item)
    store.update(mutate)
    return {"check": {**item, "kind": "ai", "action": "flag", "group": "AI", "custom": True}}


@app.delete("/api/checks/{check_id}")
def api_delete_check(check_id: str):
    def mutate(data):
        data["customChecks"] = [c for c in data["customChecks"] if c["id"] != check_id]
    store.update(mutate)
    return {"ok": True}


@app.get("/api/connections")
def api_connections():
    return {"connections": store.connections()}


@app.post("/api/connections")
def api_save_connection(body: dict):
    provider = (body.get("provider") or "").strip()
    if provider not in {"phrase", "smartcat", "xtm", "lokalise"}:
        raise HTTPException(400, "Choose Phrase, Smartcat, XTM, or Lokalise.")
    label = (body.get("label") or "").strip() or provider.title()
    creds = {
        "region": (body.get("region") or "eu").strip(),
        "server": (body.get("server") or "eu").strip(),
        "base_url": (body.get("base_url") or "").strip(),
        "username": (body.get("username") or "").strip(),
        "password": body.get("password") or "",
        "token": (body.get("token") or "").strip(),
        "otp": (body.get("otp") or "").strip(),
        "email": (body.get("email") or "").strip(),
        "account_id": (body.get("account_id") or "").strip(),
        "api_key": (body.get("api_key") or "").strip(),
        "client": (body.get("client") or "").strip(),
        "user_id": (body.get("user_id") or "").strip(),
    }
    conn_id = (body.get("id") or "").strip() or store.new_id(provider[:3])
    existing = store.get_connection(conn_id)
    if existing and not creds["password"]:
        creds["password"] = (existing.get("creds") or {}).get("password") or ""
    if existing and not creds["token"]:
        creds["token"] = (existing.get("creds") or {}).get("token") or ""
    if existing and not creds["api_key"]:
        creds["api_key"] = (existing.get("creds") or {}).get("api_key") or ""

    record = {
        "id": conn_id,
        "provider": provider,
        "label": label,
        "status": "saved",
        "account": "",
        "error": "",
        "creds": creds,
    }
    try:
        record["account"] = test_connection(provider, creds)
        record["status"] = "connected" if "Launch" not in record["account"] and "login" not in record["account"].lower() else "saved"
        if provider == "lokalise" and "saved" in record["account"].lower():
            record["status"] = "saved"
    except ProviderError as exc:
        record["status"] = "error"
        record["error"] = str(exc)
    except Exception as exc:  # noqa: BLE001
        record["status"] = "error"
        record["error"] = str(exc)

    if record["status"] == "connected":
        glossaries, gloss_error = list_glossaries(provider, creds)
        record["glossaries"] = glossaries
        if glossaries:
            record["account"] = f"{record['account']} · {len(glossaries)} glossaries"
        elif gloss_error:
            record["account"] = f"{record['account']} · {gloss_error[:160]}"

    def mutate(data):
        data["connections"] = [c for c in data["connections"] if c["id"] != conn_id]
        data["connections"].append(record)
    store.update(mutate)
    return {"connection": store.public_connection(record)}


@app.delete("/api/connections/{conn_id}")
def api_delete_connection(conn_id: str):
    if conn_id in {"demo", "local"}:
        raise HTTPException(400, "The demo workspace and local files stay available.")

    def mutate(data):
        data["connections"] = [c for c in data["connections"] if c["id"] != conn_id]
    store.update(mutate)
    return {"ok": True}


@app.get("/api/connections/{conn_id}/tms")
def api_tms(conn_id: str):
    conn = store.get_connection(conn_id)
    if conn is None:
        raise HTTPException(404, "Connection not found.")
    if conn["provider"] == "local":
        uploads = store.load()["uploads"]
        return {"tms": [
            {
                "id": item["id"],
                "name": item["name"],
                "source": item.get("source") or "",
                "targets": item.get("targets") or [],
                "units": item.get("units"),
            }
            for item in uploads
        ]}
    try:
        tms = list_remote_tms(conn["provider"], conn.get("creds") or {})
    except ProviderError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, str(exc)) from exc
    return {"tms": tms}


@app.get("/api/connections/{conn_id}/glossaries")
def api_glossaries(conn_id: str):
    conn = store.get_connection(conn_id)
    if conn is None:
        raise HTTPException(404, "Connection not found.")
    glossaries, error = list_glossaries(conn["provider"], conn.get("creds") or {})
    return {"glossaries": glossaries, "error": error}


@app.post("/api/uploads")
async def api_upload(file: UploadFile = File(...)):
    name = file.filename or "memory.tmx"
    if not name.lower().endswith(".tmx"):
        raise HTTPException(400, "Upload a .tmx file.")
    UPLOADS.mkdir(parents=True, exist_ok=True)
    item_id = store.new_id("tmx")
    dest = UPLOADS / f"{item_id}.tmx"
    dest.write_bytes(await file.read())
    try:
        tus = parse_tmx(str(dest))
    except Exception as exc:  # noqa: BLE001
        dest.unlink(missing_ok=True)
        raise HTTPException(400, f"Could not read that TMX file: {exc}") from exc
    if not tus:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, "That TMX file has no translation units.")
    langs = sorted({tu["tgt_lang"] for tu in tus if tu.get("tgt_lang")})
    record = {
        "id": item_id,
        "name": Path(name).stem,
        "file": str(dest),
        "source": tus[0].get("src_lang") or "",
        "targets": langs,
        "units": len(tus),
    }

    def mutate(data):
        data["uploads"].append(record)
    store.update(mutate)
    return {"tm": {k: record[k] for k in ("id", "name", "source", "targets", "units")}}


@app.delete("/api/uploads/{item_id}")
def api_delete_upload(item_id: str):
    def mutate(data):
        kept = []
        for item in data["uploads"]:
            if item["id"] == item_id:
                Path(item["file"]).unlink(missing_ok=True)
            else:
                kept.append(item)
        data["uploads"] = kept
    store.update(mutate)
    return {"ok": True}


@app.post("/api/glossary")
async def api_glossary(file: UploadFile = File(...)):
    name = file.filename or "glossary.xlsx"
    if not name.lower().endswith(".xlsx"):
        raise HTTPException(400, "Upload an .xlsx glossary.")
    GLOSSARY_DIR.mkdir(parents=True, exist_ok=True)
    item_id = store.new_id("tb")
    dest = GLOSSARY_DIR / f"{item_id}.xlsx"
    dest.write_bytes(await file.read())
    try:
        from webapp.checks import load_glossary
        pairs, src_col, tgt_col, _found = load_glossary(str(dest))
    except Exception as exc:  # noqa: BLE001
        dest.unlink(missing_ok=True)
        raise HTTPException(400, f"Could not read that glossary: {exc}") from exc
    record = {
        "id": item_id,
        "name": name,
        "file": str(dest),
        "entries": len(pairs),
        "sourceCol": src_col,
        "targetCol": tgt_col,
    }

    def mutate(data):
        data["glossary"] = record
    store.update(mutate)
    return {"glossary": {k: record[k] for k in ("id", "name", "entries", "sourceCol", "targetCol")}}


@app.get("/api/glossary")
def api_glossary_info():
    item = store.load().get("glossary")
    if not item:
        return {"glossary": None}
    return {"glossary": {k: item[k] for k in ("id", "name", "entries", "sourceCol", "targetCol")}}


@app.get("/api/settings")
def api_settings():
    return store.public_settings()


@app.put("/api/settings")
def api_save_settings(body: dict):
    def mutate(data):
        if "openai_api_key" in body and body["openai_api_key"] != "":
            data["settings"]["openai_api_key"] = body["openai_api_key"].strip()
        if body.get("clear_key"):
            data["settings"]["openai_api_key"] = ""
        if body.get("openai_model"):
            data["settings"]["openai_model"] = body["openai_model"]
        if body.get("llm_limit"):
            data["settings"]["llm_limit"] = max(1, min(2000, int(body["llm_limit"])))
    store.update(mutate)
    return store.public_settings()


def _load_tus(conn: dict, tm: dict) -> list[dict]:
    provider = conn["provider"]
    if provider == "demo":
        return clone_tus(tm["id"])
    if provider == "local":
        uploads = {item["id"]: item for item in store.load()["uploads"]}
        item = uploads.get(tm["id"])
        if not item:
            raise ProviderError(f"Uploaded TM not found: {tm.get('name') or tm['id']}")
        return parse_tmx(item["file"])
    path = export_remote(provider, conn.get("creds") or {}, tm)
    return parse_tmx(str(path))


def _execute(run_id: str) -> None:
    run = next(item for item in store.load()["runs"] if item["id"] == run_id)
    conn = store.get_connection(run["connectionId"])
    if conn is None:
        _finish(run_id, status="error", error="Connection no longer exists.")
        return
    catalog = check_by_id(store.load()["customChecks"])
    enabled = []
    for cid in run["checkIds"]:
        check = catalog.get(cid)
        if check:
            if check.get("kind") == "ai" and cid != "llm_severity":
                custom = next((c for c in store.load()["customChecks"] if c["id"] == cid), None)
                if custom:
                    check = {**check, "prompt": custom.get("prompt") or ""}
            elif cid == "llm_severity":
                check = {**check, "prompt": default_prompt()}
            enabled.append(check)
    settings = store.load()["settings"]
    glossary_path = _glossary_path(run)
    try:
        corpora = []
        for tm in run["tms"]:
            _patch(run_id, progress=f"Loading {tm['name']}")
            tus = _load_tus(conn, tm)
            for tu in tus:
                tu["tm_id"] = tm["id"]
                tu["tm_name"] = tm["name"]
            corpora.append((tm, tus))
        reports = []
        separate = run["mode"] in {"single", "separate"} and len(corpora) > 1
        if separate:
            for index, (tm, tus) in enumerate(corpora):
                _patch(run_id, progress=f"Checking {tm['name']}")
                reports.append(_pack_report(run_id, index, {
                    "tmId": tm["id"],
                    "tmName": tm["name"],
                    **run_corpus(tus, enabled, run.get("params") or {}, settings, glossary_path),
                }))
        else:
            merged = []
            for _tm, tus in corpora:
                merged.extend(tus)
            title = run["tmNames"][0] if len(run["tmNames"]) == 1 else " + ".join(run["tmNames"])
            _patch(run_id, progress="Checking the corpus")
            reports.append(_pack_report(run_id, 0, {
                "tmId": "combined" if len(corpora) > 1 else corpora[0][0]["id"],
                "tmName": title,
                **run_corpus(merged, enabled, run.get("params") or {}, settings, glossary_path),
            }))
    except Exception as exc:  # noqa: BLE001
        _finish(run_id, status="error", error=str(exc), progress="")
        return
    _finish(run_id, status="done", reports=reports, progress="")


def _pack_report(run_id: str, index: int, report: dict) -> dict:
    corpus = report.pop("corpus", [])
    folder = store.DATA / "corpora" / run_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{index}.json").write_text(json.dumps(corpus, ensure_ascii=False), encoding="utf-8")
    return report


def _glossary_path(run: dict) -> str | None:
    params = run.get("params") or {}
    if "glossary" not in (run.get("checkIds") or []):
        return None
    glossary_id = (params.get("glossaryId") or "").strip()
    mode = params.get("glossaryMode") or "none"
    if not glossary_id:
        if mode == "demo":
            glossary_id = "demo-es"
        elif mode == "upload":
            glossary_id = "upload"
    if not glossary_id or glossary_id == "none":
        return None
    if glossary_id == "upload":
        item = store.load().get("glossary")
        if item and Path(item["file"]).is_file():
            return item["file"]
        return None
    if glossary_id.startswith("demo-"):
        return str(store.demo_glossary_path(glossary_id))
    conn = store.get_connection(run["connectionId"])
    if conn is None:
        raise ProviderError("Connection no longer exists.")
    return str(export_glossary(conn["provider"], conn.get("creds") or {}, glossary_id))


def _patch(run_id: str, **fields) -> None:
    def mutate(data):
        for run in data["runs"]:
            if run["id"] == run_id:
                run.update(fields)
    store.update(mutate)


def _finish(run_id: str, **fields) -> None:
    _patch(run_id, **fields)


@app.post("/api/runs")
def api_create_run(body: dict):
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "Name this cleaning run.")
    conn_id = body.get("connectionId") or ""
    conn = store.get_connection(conn_id)
    if conn is None:
        raise HTTPException(400, "Choose a TMS connection.")
    tm_ids = body.get("tmIds") or []
    if not tm_ids:
        raise HTTPException(400, "Select at least one translation memory.")
    check_ids = body.get("checkIds") or []
    if not check_ids:
        raise HTTPException(400, "Select at least one check.")
    mode = body.get("mode") or "single"
    if mode == "together":
        mode = "merged"
    if mode == "separate":
        mode = "single"
    if len(tm_ids) == 1:
        mode = "single"
    elif mode not in {"merged", "single"}:
        raise HTTPException(400, "Choose merged or single.")

    try:
        listed = _listed_or_empty(conn)
    except ProviderError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, str(exc)) from exc
    known = {tm["id"]: tm for tm in listed}
    tms = []
    for tm_id in tm_ids:
        found = known.get(tm_id)
        if not found:
            raise HTTPException(400, "One of the selected memories is no longer listed. Refresh the list.")
        tms.append(found)
    catalog = check_by_id(store.load()["customChecks"])
    checks = []
    for cid in check_ids:
        item = catalog.get(cid)
        if item:
            checks.append({"id": item["id"], "name": item["name"], "kind": item.get("kind", "rule"),
                           "action": item.get("action", "remove")})
    run = {
        "id": store.new_id("run"),
        "name": name,
        "tags": _tags(body.get("tags")),
        "createdAt": store._now(),
        "mode": mode,
        "status": "running",
        "progress": "Starting",
        "error": "",
        "sample": False,
        "connectionId": conn["id"],
        "connectionLabel": conn["label"],
        "provider": conn["provider"],
        "tmNames": [tm["name"] for tm in tms],
        "tmIds": [tm["id"] for tm in tms],
        "languages": _tags(body.get("languages")),
        "linkedRunIds": [str(item) for item in (body.get("linkedRunIds") or [])][:40],
        "reviews": {},
        "tms": tms,
        "checkIds": [c["id"] for c in checks],
        "checks": checks,
        "params": {
            "before": (body.get("before") or "").strip(),
            "users": (body.get("users") or "").strip(),
            "threshold": body.get("threshold") or 80,
            "caseSensitive": bool(body.get("caseSensitive")),
            "symbolPercent": body.get("symbolPercent") or 90,
            "glossaryMode": body.get("glossaryMode") or "none",
            "glossaryId": (body.get("glossaryId") or "").strip(),
        },
        "reports": [],
    }

    def mutate(data):
        data["runs"].insert(0, run)
    store.update(mutate)
    threading.Thread(target=_execute, args=(run["id"],), daemon=True).start()
    return {"run": _public_run(run)}


def _listed_or_empty(conn: dict) -> list[dict]:
    if conn["provider"] == "local":
        return [
            {"id": item["id"], "name": item["name"], "source": item.get("source") or "",
             "targets": item.get("targets") or []}
            for item in store.load()["uploads"]
        ]
    return list_remote_tms(conn["provider"], conn.get("creds") or {})


def _tags(raw) -> list[str]:
    if isinstance(raw, list):
        parts = raw
    else:
        parts = str(raw or "").replace(";", ",").split(",")
    out = []
    for part in parts:
        text = str(part).strip()
        if text and text not in out:
            out.append(text)
    return out[:12]


def _public_run(run: dict) -> dict:
    hidden = {"tms", "checkIds", "params"}
    out = {key: value for key, value in run.items() if key not in hidden}
    out.setdefault("reviews", {})
    out.setdefault("linkedRunIds", [])
    out["hasCorpus"] = (store.DATA / "corpora" / run["id"]).is_dir()
    return out


@app.get("/api/runs")
def api_runs():
    runs = [_public_run(run) for run in store.load()["runs"]]
    runs.sort(key=lambda item: item.get("createdAt") or "", reverse=True)
    return {"runs": runs}


@app.get("/api/runs/{run_id}")
def api_run(run_id: str):
    for run in store.load()["runs"]:
        if run["id"] == run_id:
            return {"run": _public_run(run)}
    raise HTTPException(404, "Run not found.")


@app.post("/api/runs/{run_id}/review")
def api_review(run_id: str, body: dict):
    key = (body.get("key") or "").strip()
    decision = body.get("decision")
    if not key:
        raise HTTPException(400, "Missing segment.")
    if decision not in {"delete", "keep"}:
        raise HTTPException(400, "Choose delete or keep.")
    found = {"run": None}

    def mutate(data):
        for run in data["runs"]:
            if run["id"] == run_id:
                run.setdefault("reviews", {})[key] = decision
                found["run"] = run
    store.update(mutate)
    if found["run"] is None:
        raise HTTPException(404, "Run not found.")
    return {"run": _public_run(found["run"])}


@app.post("/api/runs/{run_id}/commit")
def api_commit(run_id: str):
    run = next((item for item in store.load()["runs"] if item["id"] == run_id), None)
    if run is None:
        raise HTTPException(404, "Run not found.")
    if run.get("status") != "done":
        raise HTTPException(400, "Wait until the run finishes.")
    segments = _load_corpus(run_id)
    if not segments:
        raise HTTPException(400, "This run has no TM file to upload. Sample history cannot be committed.")
    conn = store.get_connection(run["connectionId"])
    if conn is None:
        raise HTTPException(400, "Connection no longer exists.")
    reviews = run.get("reviews") or {}
    pending = 0
    grouped: dict[str, list[dict]] = {}
    for seg in segments:
        tm_id = seg.get("tm_id") or ""
        grouped.setdefault(tm_id, []).append(seg)
        if seg.get("ai") and f"{tm_id}:{seg.get('tuid')}" not in reviews:
            pending += 1
    known = {tm["id"]: tm for tm in run.get("tms") or []}
    messages = []
    out_dir = store.DATA / "commits" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        for tm_id, rows in grouped.items():
            tm = known.get(tm_id)
            if tm is None:
                messages.append(f"Skipped {len(rows)} segments with no memory id.")
                continue
            kept = [row for row in rows if _commit_keeps(row, reviews)]
            path = out_dir / f"{tm_id.replace(':', '_')}.tmx"
            _write_tmx(kept, path)
            messages.append(f"{tm['name']}: {import_remote(conn['provider'], conn.get('creds') or {}, tm, path)} ({len(kept)} of {len(rows)} segments)")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, str(exc)) from exc
    if pending:
        messages.append(f"{pending} AI flags were still undecided, so those segments stayed in the file.")
    commit = {"at": store._now(), "message": " ".join(messages)}

    def mutate(data):
        for item in data["runs"]:
            if item["id"] == run_id:
                item["commit"] = commit
    store.update(mutate)
    run = next(item for item in store.load()["runs"] if item["id"] == run_id)
    return {"run": _public_run(run)}


def _load_corpus(run_id: str) -> list[dict]:
    folder = store.DATA / "corpora" / run_id
    if not folder.is_dir():
        return []
    rows = []
    for path in sorted(folder.glob("*.json")):
        rows.extend(json.loads(path.read_text(encoding="utf-8")))
    return rows


def _commit_keeps(seg: dict, reviews: dict) -> bool:
    if seg.get("removed"):
        return False
    key = f"{seg.get('tm_id') or ''}:{seg.get('tuid')}"
    return reviews.get(key) != "delete"


def _write_tmx(segments: list[dict], path: Path) -> None:
    root = ET.Element("tmx", {"version": "1.4"})
    srclang = next((seg.get("src_lang") for seg in segments if seg.get("src_lang")), "*all*")
    ET.SubElement(root, "header", {
        "creationtool": "TM Cleaner",
        "creationtoolversion": "1.0",
        "segtype": "sentence",
        "o-tmf": "tmx",
        "adminlang": "en",
        "srclang": srclang or "*all*",
        "datatype": "plaintext",
    })
    body = ET.SubElement(root, "body")
    for seg in segments:
        tu = ET.SubElement(body, "tu", {"tuid": str(seg.get("tuid") or "")})
        for lang, text in (
            (seg.get("src_lang") or "und", seg.get("source") or ""),
            (seg.get("tgt_lang") or "und", seg.get("target") or ""),
        ):
            tuv = ET.SubElement(tu, "tuv")
            tuv.set(XML_LANG, lang)
            ET.SubElement(tuv, "seg").text = text
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


@app.delete("/api/runs/{run_id}")
def api_delete_run(run_id: str):
    def mutate(data):
        data["runs"] = [run for run in data["runs"] if run["id"] != run_id]
    store.update(mutate)
    return {"ok": True}


def _check_meta(run: dict, cid: str) -> dict:
    return next((c for c in run.get("checks") or [] if c["id"] == cid), {"name": cid, "kind": "rule"})


@app.get("/api/stats")
def api_stats(languages: str = "", errors: str = "", tags: str = "", include_sample: bool = True):
    want_langs = {part.strip() for part in languages.split("|") if part.strip()}
    want_errors = {part.strip() for part in errors.split(",") if part.strip()}
    want_tags = {part.strip() for part in tags.split(",") if part.strip()}
    points = []
    lang_acc: dict[str, dict] = {}
    err_acc: dict[str, dict] = {}
    tag_acc: dict[str, dict] = {}

    for run in store.load()["runs"]:
        if run.get("status") != "done":
            continue
        if run.get("sample") and not include_sample:
            continue
        run_tags = run.get("tags") or []
        if want_tags and not (set(run_tags) & want_tags):
            continue

        blocks = []
        for report in run.get("reports") or []:
            for block in report.get("languages") or []:
                if want_langs and block["pair"] not in want_langs:
                    continue
                blocks.append(block)
        if want_langs and not blocks:
            continue

        run_errors: dict[str, int] = {}
        if want_langs:
            original = sum(block["original"] for block in blocks)
            if want_errors:
                removed = fixed = 0
                for block in blocks:
                    for cid, count in (block.get("errors") or {}).items():
                        if cid in want_errors:
                            run_errors[cid] = run_errors.get(cid, 0) + count
                removed = sum(run_errors.values())
                fixed = 0
            else:
                removed = sum(block["removed"] for block in blocks)
                fixed = sum(block["fixed"] for block in blocks)
                for block in blocks:
                    for cid, count in (block.get("errors") or {}).items():
                        run_errors[cid] = run_errors.get(cid, 0) + count
        else:
            original = sum((report.get("original") or 0) for report in run.get("reports") or [])
            removed = fixed = 0
            for report in run.get("reports") or []:
                for check in report.get("byCheck") or []:
                    if want_errors and check["id"] not in want_errors:
                        continue
                    hit = (check.get("removed") or 0) + (check.get("fixed") or 0)
                    if not hit:
                        continue
                    run_errors[check["id"]] = run_errors.get(check["id"], 0) + hit
                    removed += check.get("removed") or 0
                    fixed += check.get("fixed") or 0
            if not want_errors:
                removed = sum((report.get("removed") or 0) for report in run.get("reports") or [])
                fixed = sum((report.get("fixed") or 0) for report in run.get("reports") or [])

        if want_errors and not run_errors:
            continue
        if not original:
            continue

        for block in blocks:
            bucket = lang_acc.setdefault(block["pair"], {
                "original": 0, "removed": 0, "fixed": 0, "errors": {}, "runs": [],
            })
            bucket["original"] += block["original"]
            bucket["removed"] += block["removed"]
            bucket["fixed"] += block["fixed"]
            for cid, count in (block.get("errors") or {}).items():
                if want_errors and cid not in want_errors:
                    continue
                bucket["errors"][cid] = bucket["errors"].get(cid, 0) + count
            bucket["runs"].append({
                "id": run["id"],
                "name": run["name"],
                "date": (run.get("createdAt") or "")[:10],
                "original": block["original"],
                "removed": block["removed"],
                "fixed": block["fixed"],
            })

        for cid, count in run_errors.items():
            meta = _check_meta(run, cid)
            slot = err_acc.setdefault(cid, {
                "id": cid,
                "name": meta.get("name") or cid,
                "kind": meta.get("kind") or "rule",
                "count": 0,
            })
            slot["count"] += count

        issues = removed + fixed
        for tag in run_tags:
            slot = tag_acc.setdefault(tag, {"tag": tag, "runs": 0, "issues": 0})
            slot["runs"] += 1
            slot["issues"] += issues
        points.append({
            "id": run["id"],
            "name": run["name"],
            "date": (run.get("createdAt") or "")[:10],
            "createdAt": run.get("createdAt"),
            "tags": run_tags,
            "original": original,
            "removed": removed,
            "fixed": fixed,
            "rate": round((issues / original * 100) if original else 0, 2),
            "sample": bool(run.get("sample")),
        })

    points.sort(key=lambda item: item.get("createdAt") or "")
    languages_out = []
    for pair, bucket in sorted(lang_acc.items(), key=lambda kv: kv[1]["original"], reverse=True):
        total = bucket["original"] or 1
        shown_removed = sum(bucket["errors"].values()) if want_errors else bucket["removed"]
        shown_fixed = 0 if want_errors else bucket["fixed"]
        languages_out.append({
            "pair": pair,
            "original": bucket["original"],
            "removed": shown_removed,
            "fixed": shown_fixed,
            "rate": round((shown_removed + shown_fixed) / total * 100, 2),
            "errors": bucket["errors"],
            "runs": bucket["runs"],
        })
    return {
        "trend": points,
        "languages": languages_out,
        "errorTypes": sorted(err_acc.values(), key=lambda item: item["count"], reverse=True),
        "tags": sorted(tag_acc.values(), key=lambda item: item["issues"], reverse=True),
    }


@app.post("/api/lokalise/login")
def api_lokalise_login(body: dict):
    if _LOGIN["busy"]:
        return {"state": dict(_LOGIN)}
    conn = store.get_connection(body.get("connectionId") or "")
    if conn is None or conn.get("provider") != "lokalise":
        raise HTTPException(400, "Save a Lokalise connection first.")
    _LOGIN.update(waiting=False, prompt="Opening the Lokalise browser…", error="", done=False, busy=True)
    threading.Thread(target=_lokalise_thread, args=(conn,), daemon=True).start()
    return {"state": dict(_LOGIN)}


@app.get("/api/lokalise/login")
def api_lokalise_state():
    return {"state": dict(_LOGIN)}


@app.post("/api/lokalise/continue")
def api_lokalise_continue():
    _LOGIN_EVENT.set()
    return {"state": dict(_LOGIN)}


def _lokalise_thread(conn: dict) -> None:
    import builtins
    os.environ["LOKALISE_TM_HOME"] = str(store.DATA / "lokalise")
    creds = conn.get("creds") or {}
    if creds.get("email"):
        os.environ["LOKALISE_EMAIL"] = creds["email"]
    if creds.get("password"):
        os.environ["LOKALISE_PASSWORD"] = creds["password"]
    try:
        from lokalise_ui import list_tms, lokalise_session
    except ImportError:
        _LOGIN.update(busy=False, error="Playwright is not installed. Run: pip install playwright && playwright install chromium", done=False)
        return

    def _wait(prompt: str = ""):
        _LOGIN["waiting"] = True
        _LOGIN["prompt"] = prompt.strip() or "Continue in the browser, then click Continue here."
        _LOGIN_EVENT.clear()
        if not _LOGIN_EVENT.wait(timeout=600):
            raise RuntimeError("Lokalise login timed out.")
        _LOGIN["waiting"] = False
        return ""

    original = builtins.input
    builtins.input = _wait
    try:
        with lokalise_session(headed=True, reset=False) as page:
            found = list_tms(page)
        _LOGIN.update(busy=False, done=True, waiting=False, error="", prompt=f"Logged in. {len(found)} TM(s) visible.")
        def mutate(data):
            for item in data["connections"]:
                if item["id"] == conn["id"]:
                    item["status"] = "connected"
                    item["account"] = f"Lokalise · {len(found)} TMs"
                    item["error"] = ""
        store.update(mutate)
    except Exception as exc:  # noqa: BLE001
        _LOGIN.update(busy=False, done=False, waiting=False, error=str(exc))
    finally:
        builtins.input = original
