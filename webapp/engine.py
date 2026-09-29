"""Run the selected checks and shape a report in the cleaning-report layout."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

from webapp.checks import (
    currencies,
    fix_end_punct,
    fix_whitespace,
    load_glossary,
    norm_space,
    numbers_only,
    only_url,
    parse_severity,
    repeated,
    symbols_only,
    urls,
)

XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"


def parse_tmx(path: str) -> list[dict]:
    root = ET.parse(path).getroot()
    if root.tag != "tmx":
        raise ValueError("Not a TMX file.")
    header = root.find("header")
    srclang = (header.get("srclang") if header is not None else "") or ""
    body = root.find("body")
    if body is None:
        raise ValueError("TMX has no body.")
    tus = []
    for i, tu in enumerate(body.findall("tu"), start=1):
        tuvs = []
        for tuv in tu.findall("tuv"):
            lang = (tuv.get(XML_LANG) or tuv.get("lang") or "").strip()
            seg = tuv.find("seg")
            text = "".join(seg.itertext()).strip() if seg is not None else ""
            tuvs.append((lang, text))
        if len(tuvs) < 2:
            continue
        src = next((pair for pair in tuvs if srclang and pair[0].lower() == srclang.lower()), tuvs[0])
        tgt = next((pair for pair in tuvs if pair is not src), tuvs[1])
        tus.append({
            "tuid": tu.get("tuid") or str(i),
            "source": src[1],
            "target": tgt[1],
            "src_lang": src[0] or srclang or "und",
            "tgt_lang": tgt[0] or "und",
            "changedate": tu.get("changedate") or tu.get("creationdate") or "",
            "changeid": tu.get("changeid") or tu.get("creationid") or "",
        })
    return tus


def _pair(tu: dict) -> str:
    return f"{tu.get('src_lang') or '?'} → {tu.get('tgt_lang') or '?'}"


def _before_date(changedate: str, cutoff: str) -> bool:
    """cutoff is YYYY-MM-DD. TMX dates look like 20200115T100000Z."""
    if not changedate or not cutoff:
        return False
    stamp = changedate.replace("-", "")[:8]
    cut = cutoff.replace("-", "")[:8]
    return stamp.isdigit() and cut.isdigit() and stamp < cut


def _glossary_hits(tu: dict, pairs, found, threshold: float, case_sensitive: bool = False) -> list[tuple[str, str]]:
    hits = []
    for src, tgt in pairs:
        if not tgt:
            continue
        if found(src, tu["source"], threshold, case_sensitive) and not found(tgt, tu["target"], threshold, case_sensitive):
            hits.append((src, tgt))
    return hits


def _judge_batch(items: list[dict], settings: dict, prompts: dict[str, str]) -> dict[tuple, str]:
    """Return {(tuid, check_id): severity}."""
    key = (settings or {}).get("openai_api_key") or ""
    if not key or not items:
        return {}
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("The openai package is not installed.") from exc
    client = OpenAI(api_key=key)
    model = (settings or {}).get("openai_model") or "gpt-4o-mini"
    limit = int((settings or {}).get("llm_limit") or 200)

    jobs = []
    for tu in items:
        for check_id, prompt in prompts.items():
            if len(jobs) >= limit:
                break
            language = tu.get("tgt_lang") or "the target language"
            text = (prompt.replace("{source}", tu.get("source") or "")
                    .replace("{target}", tu.get("target") or "")
                    .replace("{language}", language))
            jobs.append((tu["tuid"], check_id, text))
        if len(jobs) >= limit:
            break

    def one(job):
        tuid, check_id, text = job
        response = client.chat.completions.create(
            model=model,
            temperature=0.1,
            messages=[{"role": "user", "content": text}],
            timeout=120,
        )
        content = response.choices[0].message.content or ""
        return tuid, check_id, parse_severity(content), content.strip()

    found: dict[tuple, tuple[str, str]] = {}
    workers = 6
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(one, job) for job in jobs]
        for future in as_completed(futures):
            tuid, check_id, level, raw = future.result()
            found[(tuid, check_id)] = (level, raw)
    return found


def run_corpus(
    tus: list[dict],
    enabled: list[dict],
    params: dict,
    settings: dict | None = None,
    glossary_path: str | None = None,
) -> dict:
    params = params or {}
    selected = {c["id"]: c for c in enabled}
    rules = [c for c in enabled if c.get("kind") != "ai"]
    ai_checks = [c for c in enabled if c.get("kind") == "ai"]

    glossary_pairs = []
    glossary_found = None
    glossary_note = ""
    threshold = float(params.get("threshold") or 80)
    case_sensitive = bool(params.get("caseSensitive"))
    try:
        symbol_ratio = float(params.get("symbolPercent") or 90) / 100.0
    except (TypeError, ValueError):
        symbol_ratio = 0.90
    symbol_ratio = min(1.0, max(0.5, symbol_ratio))
    if "glossary" in selected:
        if glossary_path:
            glossary_pairs, src_col, tgt_col, glossary_found = load_glossary(glossary_path)
            case_note = "case-sensitive" if case_sensitive else "case-insensitive"
            glossary_note = f"{len(glossary_pairs)} terms ({src_col} → {tgt_col}), threshold {threshold:g}%, {case_note}"
        else:
            glossary_note = "No glossary file was provided."

    prepared = []
    seen: dict[str, str] = {}
    for tu in tus:
        prepared.append({
            **tu,
            "source_orig": tu["source"],
            "target_orig": tu["target"],
            "fixes": [],
            "removals": [],
            "details": [],
            "ai": [],
        })

    # Duplicates are decided on the original pair, keeping the first TU.
    if "duplicates" in selected:
        for tu in prepared:
            key = norm_space(tu["source"]).casefold() + "\n" + norm_space(tu["target"]).casefold()
            if not norm_space(tu["source"]) and not norm_space(tu["target"]):
                continue
            if key in seen:
                tu["removals"].append("duplicates")
                tu["details"].append(f"Duplicate of segment {seen[key]}")
            else:
                seen[key] = tu["tuid"]

    for tu in prepared:
        if "whitespace" in selected:
            src, tgt = fix_whitespace(tu["source"]), fix_whitespace(tu["target"])
            if (src, tgt) != (tu["source"], tu["target"]):
                tu["source"], tu["target"] = src, tgt
                tu["fixes"].append("whitespace")
        if "end_punct" in selected:
            tgt = fix_end_punct(tu["source"], tu["target"])
            if tgt != tu["target"]:
                tu["target"] = tgt
                tu["fixes"].append("end_punct")
        if "empty" in selected and (not tu["source"].strip() or not tu["target"].strip()):
            tu["removals"].append("empty")
        if "source_eq_target" in selected and tu["source"].strip() and tu["source"].strip().casefold() == tu["target"].strip().casefold():
            tu["removals"].append("source_eq_target")
        if "numbers_only" in selected and (numbers_only(tu["source"]) or numbers_only(tu["target"])):
            tu["removals"].append("numbers_only")
        if "symbols_only" in selected and (symbols_only(tu["source"], symbol_ratio) or symbols_only(tu["target"], symbol_ratio)):
            tu["removals"].append("symbols_only")
        if "urls_only" in selected and (only_url(tu["source"]) or only_url(tu["target"])):
            tu["removals"].append("urls_only")
        if "repeated_words" in selected and (repeated(tu["source_orig"]) or repeated(tu["target_orig"])):
            tu["removals"].append("repeated_words")
            m = re.search(r"\b([\w'-]{2,})\s+\1\b", tu["source_orig"] + " " + tu["target_orig"], re.I)
            if m:
                tu["details"].append(f"Repeated word “{m.group(1)}”")
        if "currency_mismatch" in selected:
            src_c, tgt_c = currencies(tu["source"]), currencies(tu["target"])
            if src_c and src_c != tgt_c:
                tu["removals"].append("currency_mismatch")
                tu["details"].append(f"Currency {', '.join(sorted(src_c)) or '—'} → {', '.join(sorted(tgt_c)) or '—'}")
        if "url_mismatch" in selected:
            src_u, tgt_u = urls(tu["source"]), urls(tu["target"])
            if src_u and src_u != tgt_u:
                tu["removals"].append("url_mismatch")
                tu["details"].append("URL list differs")
        if "stale_date" in selected:
            cutoff = (params.get("before") or "").strip()
            if cutoff and _before_date(tu.get("changedate") or "", cutoff):
                tu["removals"].append("stale_date")
                tu["details"].append(f"Updated {tu.get('changedate') or 'unknown'}")
        if "modified_by" in selected:
            users = [u.strip().casefold() for u in (params.get("users") or "").split(",") if u.strip()]
            who = (tu.get("changeid") or "").casefold()
            if users and who and who in users:
                tu["removals"].append("modified_by")
                tu["details"].append(f"Modified by {tu.get('changeid')}")
        if glossary_found is not None:
            hits = _glossary_hits(
                {"source": tu["source_orig"], "target": tu["target_orig"]},
                glossary_pairs, glossary_found, threshold, case_sensitive,
            )
            if hits:
                tu["removals"].append("glossary")
                for src, tgt in hits[:3]:
                    tu["details"].append(f"TB {src} → {tgt}")

    ai_notes = []
    ai_results: dict[tuple, tuple[str, str]] = {}
    if ai_checks:
        key = (settings or {}).get("openai_api_key") or ""
        if not key:
            ai_notes.append("AI checks were not run. Add an API key in the cleaner to call the model.")
        else:
            prompts = {}
            for check in ai_checks:
                prompts[check["id"]] = check.get("prompt") or ""
            try:
                judge_items = [
                    {**tu, "source": tu["source_orig"], "target": tu["target_orig"]}
                    for tu in prepared
                ]
                ai_results = _judge_batch(judge_items, settings or {}, prompts)
                limit = int((settings or {}).get("llm_limit") or 200)
                needed = len(prepared) * len(ai_checks)
                if needed > limit:
                    ai_notes.append(f"AI checks covered the first {limit} segments of this corpus.")
            except Exception as exc:  # noqa: BLE001 - surfaced on the report
                ai_notes.append(f"AI checks failed: {exc}")

    ai_rows = []
    labels = {
        "critical": "Critical error",
        "major": "Major error",
        "minor": "Minor error",
        "correct": "Correct",
        "unparsed": "Unparsed AI output",
    }
    for tu in prepared:
        for check in ai_checks:
            payload = ai_results.get((tu["tuid"], check["id"]))
            if not payload:
                continue
            level, raw = payload
            if level == "correct":
                continue
            label = labels.get(level, level)
            raw_text = (raw or "").strip()
            if level == "unparsed":
                reason = f"Unparsed AI output. The model returned: {raw_text[:240]}"
            elif raw_text and raw_text.lower() != label.lower():
                reason = f"{label}. The model returned: {raw_text[:240]}"
            else:
                reason = f"{label}."
            flag = {"check": check["id"], "name": check["name"], "level": level, "reason": reason}
            tu["ai"].append(flag)
            tu["details"].append(f"{check['name']}: {reason}")
            ai_rows.append({
                "key": f"{tu.get('tm_id') or ''}:{tu['tuid']}",
                "tuid": tu["tuid"],
                "tmId": tu.get("tm_id") or "",
                "tmName": tu.get("tm_name") or "",
                "source": tu["source_orig"],
                "target": tu["target_orig"],
                "pair": _pair(tu),
                "check": check["id"],
                "checkName": check["name"],
                "level": level,
                "reason": reason,
                "alreadyRemoved": bool(tu["removals"]),
            })

    counts = {cid: {"removed": 0, "fixed": 0, "flagged": 0} for cid in selected}
    for tu in prepared:
        for cid in tu["removals"]:
            if cid in counts:
                counts[cid]["removed"] += 1
        for cid in tu["fixes"]:
            if cid in counts:
                counts[cid]["fixed"] += 1
        for flag in tu["ai"]:
            if flag["check"] in counts:
                counts[flag["check"]]["flagged"] += 1

    rows = []
    lang = defaultdict(lambda: {"original": 0, "removed": 0, "fixed": 0, "errors": defaultdict(int)})
    removed = fixed = 0
    for tu in prepared:
        pair = _pair(tu)
        lang[pair]["original"] += 1
        if tu["removals"]:
            status = "removed"
            removed += 1
            lang[pair]["removed"] += 1
        elif tu["fixes"]:
            status = "fixed"
            fixed += 1
            lang[pair]["fixed"] += 1
        elif tu["ai"]:
            status = "flagged"
        else:
            status = "kept"
        for cid in set(tu["removals"] + tu["fixes"] + [flag["check"] for flag in tu["ai"]]):
            lang[pair]["errors"][cid] += 1
        if status != "kept":
            rows.append({
                "tuid": tu["tuid"],
                "source": tu["source_orig"],
                "target": tu["target_orig"],
                "pair": pair,
                "status": status,
                "checks": tu["removals"] + [c for c in tu["fixes"] if c not in tu["removals"]] + [flag["check"] for flag in tu["ai"]],
                "detail": "; ".join(dict.fromkeys(tu["details"])),
            })

    original = len(prepared)
    final = original - removed
    untouched = final - fixed

    def applied(check: dict) -> tuple[bool, str]:
        if check["id"] == "glossary" and glossary_found is None:
            return False, glossary_note or "No glossary file."
        if check["id"] == "stale_date" and not (params.get("before") or "").strip():
            return False, "No cutoff date."
        if check["id"] == "modified_by" and not (params.get("users") or "").strip():
            return False, "No user names."
        if check.get("kind") == "ai" and not ai_results and ai_notes:
            return False, ai_notes[0]
        return True, ""

    by_check = []
    not_applied = []
    for check in enabled:
        ok, reason = applied(check)
        item = {
            "id": check["id"],
            "name": check["name"],
            "kind": check.get("kind", "rule"),
            "action": "flag" if check.get("kind") == "ai" else check.get("action", "remove"),
            "removed": counts.get(check["id"], {}).get("removed", 0) if ok else 0,
            "fixed": counts.get(check["id"], {}).get("fixed", 0) if ok else 0,
            "flagged": counts.get(check["id"], {}).get("flagged", 0) if ok else 0,
            "applied": ok,
            "reason": reason,
        }
        by_check.append(item)
        if not ok:
            not_applied.append(f"{check['name']} ({reason})" if reason else check["name"])

    glossary_block = None
    g = counts.get("glossary")
    if g and any(c["id"] == "glossary" and c["applied"] for c in by_check):
        flagged = g["removed"]
        retained = original - flagged
        glossary_block = {
            "original": original,
            "flagged": flagged,
            "final": retained,
            "note": glossary_note,
        }

    return {
        "original": original,
        "removed": removed,
        "fixed": fixed,
        "untouched": untouched,
        "final": final,
        "byCheck": by_check,
        "notApplied": not_applied,
        "notes": ai_notes,
        "languages": [
            {
                "pair": pair,
                "original": data["original"],
                "removed": data["removed"],
                "fixed": data["fixed"],
                "errors": dict(data["errors"]),
            }
            for pair, data in sorted(lang.items())
        ],
        "rows": rows[:200],
        "rowCount": len(rows),
        "glossary": glossary_block,
        "aiRows": ai_rows[:2000],
        "aiCount": len(ai_rows),
        "corpus": [
            {
                "tuid": tu["tuid"],
                "tm_id": tu.get("tm_id") or "",
                "source": tu["source"],
                "target": tu["target"],
                "src_lang": tu.get("src_lang") or "",
                "tgt_lang": tu.get("tgt_lang") or "",
                "removed": bool(tu["removals"]),
                "ai": [flag["check"] for flag in tu["ai"]],
            }
            for tu in prepared
        ],
    }
