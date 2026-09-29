"""JSON persistence for connections, checks, runs, and settings."""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DB_PATH = DATA / "db.json"
_LOCK = threading.Lock()

BUILTIN = [
    {
        "id": "demo",
        "provider": "demo",
        "label": "Demo workspace",
        "status": "connected",
        "account": "Three sample translation memories",
        "creds": {},
    },
    {
        "id": "local",
        "provider": "local",
        "label": "Local TMX",
        "status": "connected",
        "account": "Files you upload",
        "creds": {},
    },
]


def _now() -> str:
    return datetime.now().replace(microsecond=0).isoformat(timespec="seconds")


def _empty() -> dict:
    return {
        "connections": [],
        "customChecks": [],
        "uploads": [],
        "glossary": None,
        "settings": {"openai_api_key": "", "openai_model": "gpt-4o-mini", "llm_limit": 200},
        "runs": [],
        "seeded": False,
    }


def _read() -> dict:
    DATA.mkdir(parents=True, exist_ok=True)
    if not DB_PATH.exists():
        data = _empty()
        _write(data)
        return data
    try:
        data = json.loads(DB_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = _empty()
    for key, value in _empty().items():
        data.setdefault(key, value)
    return data


def _write(data: dict) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    tmp = DB_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(DB_PATH)


def load() -> dict:
    with _LOCK:
        return _read()


def update(mutator):
    with _LOCK:
        data = _read()
        mutator(data)
        _write(data)
        return data


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def public_settings(data: dict | None = None) -> dict:
    settings = (data or load())["settings"]
    return {
        "hasKey": bool(settings.get("openai_api_key")),
        "openai_model": settings.get("openai_model") or "gpt-4o-mini",
        "llm_limit": int(settings.get("llm_limit") or 200),
    }


def public_connection(conn: dict) -> dict:
    creds = conn.get("creds") or {}
    return {
        "id": conn["id"],
        "provider": conn["provider"],
        "label": conn["label"],
        "status": conn.get("status") or "saved",
        "account": conn.get("account") or "",
        "error": conn.get("error") or "",
        "fields": {
            "region": creds.get("region") or "",
            "server": creds.get("server") or "",
            "base_url": creds.get("base_url") or "",
            "username": creds.get("username") or "",
            "email": creds.get("email") or "",
            "account_id": creds.get("account_id") or "",
            "client": creds.get("client") or "",
            "user_id": creds.get("user_id") or "",
            "hasPassword": bool(creds.get("password")),
            "hasToken": bool(creds.get("token") or creds.get("api_key")),
        },
    }


def connections() -> list[dict]:
    data = load()
    stored = {c["id"]: c for c in data["connections"]}
    ordered = []
    for item in BUILTIN:
        ordered.append(public_connection(stored.get(item["id"], item)))
    for conn in data["connections"]:
        if conn["id"] not in {b["id"] for b in BUILTIN}:
            ordered.append(public_connection(conn))
    return ordered


def get_connection(conn_id: str) -> dict | None:
    if conn_id in {b["id"] for b in BUILTIN}:
        data = load()
        for conn in data["connections"]:
            if conn["id"] == conn_id:
                return conn
        return next(dict(b) for b in BUILTIN if b["id"] == conn_id)
    for conn in load()["connections"]:
        if conn["id"] == conn_id:
            return conn
    return None


def _report(original, removed, fixed, checks, languages, rows, glossary=None, not_applied=None):
    final = original - removed
    return {
        "original": original,
        "removed": removed,
        "fixed": fixed,
        "untouched": final - fixed,
        "final": final,
        "byCheck": checks,
        "notApplied": not_applied or [],
        "notes": [],
        "languages": languages,
        "rows": rows,
        "rowCount": len(rows),
        "glossary": glossary,
    }


def _ck(cid, name, kind, action, removed=0, fixed=0):
    return {
        "id": cid, "name": name, "kind": kind, "action": action,
        "removed": removed, "fixed": fixed, "applied": True, "reason": "",
    }


def _row(tuid, source, target, pair, status, checks, detail):
    return {
        "tuid": tuid, "source": source, "target": target, "pair": pair,
        "status": status, "checks": checks, "detail": detail,
    }


def seed_runs() -> list[dict]:
    """Illustrative history so trends and language drill-ins are visible immediately."""
    today = datetime.now().replace(hour=9, minute=0, second=0, microsecond=0)

    def at(days_ago: int) -> str:
        return (today - timedelta(days=days_ago)).isoformat(timespec="seconds")

    ui_checks = [
        _ck("numbers_only", "Numbers only", "rule", "remove", 42, 0),
        _ck("symbols_only", "Symbols only", "rule", "remove", 28, 0),
        _ck("duplicates", "Remove duplicates", "rule", "remove", 19, 0),
        _ck("empty", "Remove empty", "rule", "remove", 7, 0),
        _ck("whitespace", "Whitespace fixer", "rule", "fix", 0, 86),
        _ck("end_punct", "End punctuation fixer", "rule", "fix", 0, 64),
        _ck("glossary", "Glossary compliance", "rule", "remove", 11, 0),
    ]
    mkt_checks = [
        _ck("end_punct", "End punctuation fixer", "rule", "fix", 0, 120),
        _ck("whitespace", "Whitespace fixer", "rule", "fix", 0, 54),
        _ck("urls_only", "Remove URLs", "rule", "remove", 16, 0),
        _ck("url_mismatch", "URL mismatch", "rule", "remove", 9, 0),
        _ck("repeated_words", "No repeated words", "rule", "remove", 6, 0),
        _ck("llm_severity", "Error severity", "ai", "judge", 14, 22),
    ]
    legal_checks = [
        _ck("source_eq_target", "Remove source=target", "rule", "remove", 31, 0),
        _ck("currency_mismatch", "Currency mismatch", "rule", "remove", 8, 0),
        _ck("stale_date", "Remove TUs last updated before a date", "rule", "remove", 44, 0),
        _ck("empty", "Remove empty", "rule", "remove", 5, 0),
        _ck("llm_severity", "Error severity", "ai", "judge", 18, 11),
    ]

    def run(name, days, tags, provider, label, mode, tms, reports, sample_checks):
        return {
            "id": new_id("run"),
            "name": name,
            "tags": tags,
            "createdAt": at(days),
            "mode": mode,
            "status": "done",
            "progress": "",
            "error": "",
            "sample": True,
            "connectionId": "demo",
            "connectionLabel": label,
            "provider": provider,
            "tmNames": tms,
            "checks": [
                {"id": c["id"], "name": c["name"], "kind": c["kind"], "action": c["action"]}
                for c in sample_checks
            ],
            "reports": reports,
        }

    es = _report(
        4200, 107, 150,
        ui_checks,
        [{"pair": "en-US → es-419", "original": 4200, "removed": 107, "fixed": 150,
          "errors": {"numbers_only": 42, "symbols_only": 28, "duplicates": 19, "empty": 7,
                     "whitespace": 86, "end_punct": 64, "glossary": 11}}],
        [
            _row("14", "42", "42", "en-US → es-419", "removed", ["numbers_only"], "Numbers only"),
            _row("88", ">>>", ">>>", "en-US → es-419", "removed", ["symbols_only"], ""),
            _row("102", "Log in to the dashboard", "Entrar al tablero", "en-US → es-419", "removed",
                 ["glossary"], "TB Log in → Iniciar sesión"),
            _row("240", "  Save changes  ", "Guardar cambios", "en-US → es-419", "fixed",
                 ["whitespace"], ""),
        ],
        glossary={"original": 4200, "flagged": 11, "final": 4189, "note": "Demo EN–ES glossary, threshold 80%"},
    )
    fr = _report(
        2800, 45, 174,
        mkt_checks,
        [{"pair": "en-US → fr-FR", "original": 2800, "removed": 45, "fixed": 174,
          "errors": {"end_punct": 120, "whitespace": 54, "urls_only": 16, "url_mismatch": 9,
                     "repeated_words": 6, "llm_severity": 36}}],
        [
            _row("3", "See the lookbook!", "Voir le lookbook", "en-US → fr-FR", "fixed", ["end_punct"], ""),
            _row("19", "https://campaign.example", "https://campaign.example", "en-US → fr-FR", "removed",
                 ["urls_only"], ""),
            _row("40", "Join the the list", "Rejoindre la liste", "en-US → fr-FR", "removed",
                 ["repeated_words"], "Repeated word “the”"),
            _row("77", "A quieter kind of luxury.", "Un bruit plus calme.", "en-US → fr-FR", "removed",
                 ["llm_severity"], "Error severity: Major error"),
        ],
    )
    de = _report(
        1900, 106, 11,
        legal_checks,
        [{"pair": "en-GB → de-DE", "original": 1900, "removed": 106, "fixed": 11,
          "errors": {"source_eq_target": 31, "currency_mismatch": 8, "stale_date": 44,
                     "empty": 5, "llm_severity": 29}}],
        [
            _row("2", "Liability", "Liability", "en-GB → de-DE", "removed", ["source_eq_target"], ""),
            _row("8", "Fees are billed in GBP.", "Die Gebühren werden in EUR berechnet.", "en-GB → de-DE",
                 "removed", ["currency_mismatch"], "Currency GBP → EUR"),
            _row("30", "Either party may terminate.", "Jede Partei kann kündigen.", "en-GB → de-DE",
                 "removed", ["stale_date"], "Updated 20190601T100000Z"),
            _row("41", "Notice period", "Kündigungsfrist fehlt.", "en-GB → de-DE", "fixed",
                 ["llm_severity"], "Error severity: Minor error"),
        ],
    )
    it = _report(
        960, 38, 70,
        [
            _ck("empty", "Remove empty", "rule", "remove", 4, 0),
            _ck("duplicates", "Remove duplicates", "rule", "remove", 12, 0),
            _ck("whitespace", "Whitespace fixer", "rule", "fix", 0, 40),
            _ck("end_punct", "End punctuation fixer", "rule", "fix", 0, 30),
            _ck("llm_severity", "Error severity", "ai", "judge", 22, 9),
        ],
        [{"pair": "en-US → it-IT", "original": 960, "removed": 38, "fixed": 70,
          "errors": {"empty": 4, "duplicates": 12, "whitespace": 40, "end_punct": 30, "llm_severity": 31}}],
        [
            _row("5", "Checkout", "Checkout", "en-US → it-IT", "removed", ["duplicates"], "Duplicate of segment 1"),
            _row("12", "Your order is confirmed.", "Il tuo ordine è confermato", "en-US → it-IT", "fixed",
                 ["end_punct"], ""),
        ],
    )
    ja = _report(
        1500, 61, 40,
        [
            _ck("numbers_only", "Numbers only", "rule", "remove", 20, 0),
            _ck("symbols_only", "Symbols only", "rule", "remove", 15, 0),
            _ck("url_mismatch", "URL mismatch", "rule", "remove", 6, 0),
            _ck("whitespace", "Whitespace fixer", "rule", "fix", 0, 40),
            _ck("llm_severity", "Error severity", "ai", "judge", 20, 8),
        ],
        [{"pair": "en-US → ja-JP", "original": 1500, "removed": 61, "fixed": 40,
          "errors": {"numbers_only": 20, "symbols_only": 15, "url_mismatch": 6,
                     "whitespace": 40, "llm_severity": 28}}],
        [
            _row("9", "100%", "100％", "en-US → ja-JP", "removed", ["numbers_only"], ""),
            _row("18", "Learn more at https://help.example", "詳細は https://help.example/jp", "en-US → ja-JP",
                 "removed", ["url_mismatch"], "URL list differs"),
        ],
    )
    together = _report(
        6100, 151, 161,
        [
            _ck("numbers_only", "Numbers only", "rule", "remove", 42, 0),
            _ck("symbols_only", "Symbols only", "rule", "remove", 28, 0),
            _ck("source_eq_target", "Remove source=target", "rule", "remove", 31, 0),
            _ck("currency_mismatch", "Currency mismatch", "rule", "remove", 8, 0),
            _ck("empty", "Remove empty", "rule", "remove", 12, 0),
            _ck("duplicates", "Remove duplicates", "rule", "remove", 19, 0),
            _ck("glossary", "Glossary compliance", "rule", "remove", 11, 0),
            _ck("whitespace", "Whitespace fixer", "rule", "fix", 0, 90),
            _ck("end_punct", "End punctuation fixer", "rule", "fix", 0, 71),
        ],
        [
            {"pair": "en-US → es-419", "original": 4200, "removed": 96, "fixed": 140,
             "errors": {"numbers_only": 42, "symbols_only": 28, "duplicates": 19, "glossary": 11,
                        "whitespace": 80, "end_punct": 60}},
            {"pair": "en-GB → de-DE", "original": 1900, "removed": 55, "fixed": 21,
             "errors": {"source_eq_target": 31, "currency_mismatch": 8, "empty": 5,
                        "whitespace": 10, "end_punct": 11}},
        ],
        [
            _row("14", "42", "42", "en-US → es-419", "removed", ["numbers_only"], ""),
            _row("2", "Liability", "Liability", "en-GB → de-DE", "removed", ["source_eq_target"], ""),
            _row("102", "Log in to the dashboard", "Entrar al tablero", "en-US → es-419", "removed",
                 ["glossary"], "TB Log in → Iniciar sesión"),
        ],
        glossary={"original": 6100, "flagged": 11, "final": 6089, "note": "Demo EN–ES glossary, threshold 80%"},
    )

    return [
        run("Q2 product UI", 160, ["product", "ui"], "demo", "Demo workspace", "single",
            ["Product UI"], [{"tmId": "demo:ui", "tmName": "Product UI", **es}], ui_checks),
        run("Spring campaign", 120, ["marketing"], "demo", "Demo workspace", "single",
            ["Spring campaign"], [{"tmId": "demo:mkt", "tmName": "Spring campaign", **fr}], mkt_checks),
        run("Policy refresh", 90, ["legal"], "demo", "Demo workspace", "single",
            ["Policy clauses"], [{"tmId": "demo:legal", "tmName": "Policy clauses", **de}], legal_checks),
        run("Checkout Italian", 55, ["product", "quarterly"], "demo", "Demo workspace", "single",
            ["Checkout IT"], [{"tmId": "demo:it", "tmName": "Checkout IT", **it}], it["byCheck"]),
        run("Help center JA", 28, ["ui", "quarterly"], "demo", "Demo workspace", "single",
            ["Help center"], [{"tmId": "demo:ja", "tmName": "Help center", **ja}], ja["byCheck"]),
        run("UI and policy together", 12, ["product", "legal"], "demo", "Demo workspace", "together",
            ["Product UI", "Policy clauses"],
            [{"tmId": "combined", "tmName": "Product UI + Policy clauses", **together}], together["byCheck"]),
    ]


def ensure_seed() -> None:
    def mutate(data):
        if data.get("seeded"):
            return
        data["runs"] = seed_runs() + data["runs"]
        data["seeded"] = True
    update(mutate)
    _ensure_demo_glossary()


def _write_sheet(path: Path, rows: list[tuple[str, str]]) -> None:
    import openpyxl
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Glossary"
    sheet.append(["Source", "Target"])
    for row in rows:
        sheet.append(list(row))
    path.parent.mkdir(parents=True, exist_ok=True)
    book.save(path)


def _ensure_demo_glossary() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    es = DATA / "demo_glossary.xlsx"
    fr = DATA / "demo_glossary_fr.xlsx"
    if not es.exists():
        _write_sheet(es, [("Log in", "Iniciar sesión"), ("dashboard", "panel")])
    if not fr.exists():
        _write_sheet(fr, [("lookbook", "catalogue"), ("Shop the edit", "Découvrir la sélection")])


def demo_glossary_path(glossary_id: str = "demo-es") -> Path:
    _ensure_demo_glossary()
    if glossary_id == "demo-fr":
        return DATA / "demo_glossary_fr.xlsx"
    return DATA / "demo_glossary.xlsx"


def list_demo_glossaries() -> list[dict]:
    _ensure_demo_glossary()
    return [
        {"id": "demo-es", "name": "Product EN–ES", "languages": ["en-US", "es-419"], "entries": 2},
        {"id": "demo-fr", "name": "Campaign EN–FR", "languages": ["en-US", "fr-FR"], "entries": 2},
    ]
