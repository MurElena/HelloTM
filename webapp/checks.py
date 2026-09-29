"""Cleaning checks.

Deterministic checks follow the filter list printed by the TM cleaning report
(the same report Merge_reports.py decorates). Glossary compliance calls
TMX_TBcompliance.py. The built-in AI check uses the severity prompt from the
GPT cleaning script, with the target language filled in.
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TB_PATH = Path(r"C:\Users\arien\OneDrive\Desktop\CustomMT\scripts\TM cleaning\TMX_TBcompliance.py")

SEVERITY_PROMPT = """Please detect errors in the translation into {language}. Output the error severity according to the following scale:
- Correct
- Minor error
- Major error
- Critical error
Do not provide comments or explanations, only return the error severity level without dashes and quotation marks.
###Source sentence###
{source}
###Translation###
{target}
Your output:
"""

BUILTIN_CHECKS = [
    {
        "id": "empty",
        "name": "Remove empty",
        "detail": "Source or target blank",
        "kind": "rule",
        "action": "remove",
        "group": "Deterministic",
    },
    {
        "id": "duplicates",
        "name": "Remove duplicates",
        "detail": "Identical source and target",
        "kind": "rule",
        "action": "remove",
        "group": "Deterministic",
    },
    {
        "id": "source_eq_target",
        "name": "Remove source=target",
        "detail": "Source and target are the same text",
        "kind": "rule",
        "action": "remove",
        "group": "Deterministic",
    },
    {
        "id": "numbers_only",
        "name": "Numbers only",
        "detail": "Segment contains only numbers and number punctuation",
        "kind": "rule",
        "action": "remove",
        "group": "Deterministic",
    },
    {
        "id": "symbols_only",
        "name": "Symbols only",
        "detail": "Most of the segment is non-alphabetic. Default is 90%.",
        "kind": "rule",
        "action": "remove",
        "group": "Deterministic",
        "param": "symbols",
    },
    {
        "id": "urls_only",
        "name": "Remove URLs",
        "detail": "Source or target is only a URL",
        "kind": "rule",
        "action": "remove",
        "group": "Deterministic",
    },
    {
        "id": "repeated_words",
        "name": "No repeated words",
        "detail": "The same word appears twice in a row",
        "kind": "rule",
        "action": "remove",
        "group": "Deterministic",
    },
    {
        "id": "currency_mismatch",
        "name": "Currency mismatch",
        "detail": "Currency symbols or codes differ between source and target",
        "kind": "rule",
        "action": "remove",
        "group": "Deterministic",
    },
    {
        "id": "url_mismatch",
        "name": "URL mismatch",
        "detail": "Source and target URL lists do not match",
        "kind": "rule",
        "action": "remove",
        "group": "Deterministic",
    },
    {
        "id": "stale_date",
        "name": "Remove TUs last updated before a date",
        "detail": "Uses the TMX changedate attribute",
        "kind": "rule",
        "action": "remove",
        "group": "Deterministic",
        "param": "before",
    },
    {
        "id": "modified_by",
        "name": "Remove TUs last modified by user(s)",
        "detail": "Uses the TMX changeid attribute",
        "kind": "rule",
        "action": "remove",
        "group": "Deterministic",
        "param": "users",
    },
    {
        "id": "whitespace",
        "name": "Whitespace fixer",
        "detail": "Trim ends, collapse spaces, drop space before punctuation",
        "kind": "rule",
        "action": "fix",
        "group": "Deterministic",
    },
    {
        "id": "end_punct",
        "name": "End punctuation fixer",
        "detail": "Align the target's final . ! or ? with the source",
        "kind": "rule",
        "action": "fix",
        "group": "Deterministic",
    },
    {
        "id": "glossary",
        "name": "Glossary compliance",
        "detail": "Fuzzy termbase check from TMX_TBcompliance.py",
        "kind": "rule",
        "action": "remove",
        "group": "Deterministic",
        "param": "glossary",
    },
    {
        "id": "llm_severity",
        "name": "Error severity",
        "detail": "Finds issues and flags them. It does not remove or edit the segment.",
        "kind": "ai",
        "action": "flag",
        "group": "AI",
        "builtin": True,
    },
]

_URL = re.compile(r"https?://[^\s<>\"']+", re.I)
_CURRENCY = re.compile(r"(?:[$€£¥₹])|(?:\b(?:USD|EUR|GBP|JPY|CHF|CAD|AUD|INR)\b)", re.I)
_REPEAT = re.compile(r"\b([\w'-]{2,})\s+\1\b", re.I)
_NUM = re.compile(r"^[\d\s.,:+\-/\\%#()]+$")
_ONLY_URL = re.compile(r"^https?://\S+$", re.I)


def check_by_id(custom: list[dict]) -> dict[str, dict]:
    found = {c["id"]: dict(c) for c in BUILTIN_CHECKS}
    for item in custom:
        found[item["id"]] = {
            **item,
            "kind": "ai",
            "action": "flag",
            "group": "AI",
            "custom": True,
        }
    return found


def default_prompt(language: str = "the target language") -> str:
    return SEVERITY_PROMPT.format(language=language, source="{source}", target="{target}")


def load_tb_module():
    if not TB_PATH.is_file():
        raise RuntimeError(f"Glossary script not found: {TB_PATH}")
    spec = importlib.util.spec_from_file_location("tmx_tbcompliance", TB_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load TMX_TBcompliance.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_glossary(path: str):
    mod = load_tb_module()
    pairs, src_col, tgt_col = mod.load_glossary(path)
    return pairs, src_col, tgt_col, mod.term_found_in_text


def norm_space(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def fix_whitespace(text: str) -> str:
    text = (text or "").replace("\u00a0", " ")
    text = re.sub(r"[ \t]+", " ", text.strip())
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    text = re.sub(r"([(\[{])\s+", r"\1", text)
    text = re.sub(r"\s+([)\]}])", r"\1", text)
    return text


def end_char(text: str) -> str:
    text = (text or "").rstrip()
    return text[-1] if text and text[-1] in ".!?" else ""


def fix_end_punct(source: str, target: str) -> str:
    src_end = end_char(source)
    tgt = (target or "").rstrip()
    tgt_end = end_char(tgt)
    if src_end and tgt_end and src_end != tgt_end:
        return tgt[:-1] + src_end
    if src_end and not tgt_end and tgt:
        return tgt + src_end
    if tgt_end and not src_end:
        return tgt[:-1].rstrip()
    return target


def numbers_only(text: str) -> bool:
    text = (text or "").strip()
    return bool(text) and bool(_NUM.fullmatch(text)) and bool(re.search(r"\d", text))


def symbols_only(text: str, non_alpha: float = 0.90) -> bool:
    text = (text or "").strip()
    if len(text) < 1 or numbers_only(text):
        return False
    alpha = sum(1 for ch in text if ch.isalpha())
    return (1 - alpha / len(text)) >= non_alpha


def only_url(text: str) -> bool:
    return bool(_ONLY_URL.fullmatch((text or "").strip()))


def urls(text: str) -> list[str]:
    return [u.rstrip(".,);]") for u in _URL.findall(text or "")]


def currencies(text: str) -> set[str]:
    found = set()
    for match in _CURRENCY.findall(text or ""):
        token = match.upper()
        found.add({"$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY", "₹": "INR"}.get(token, token))
    return found


def repeated(text: str) -> bool:
    return bool(_REPEAT.search(text or ""))


def parse_severity(text: str) -> str:
    raw = (text or "").strip().lower()
    if "critical" in raw:
        return "critical"
    if "major" in raw:
        return "major"
    if "minor" in raw:
        return "minor"
    if "correct" in raw:
        return "correct"
    return "unparsed"


def severity_status(level: str) -> str | None:
    """Map an AI severity onto the report's removed / fixed buckets."""
    if level in ("critical", "major"):
        return "removed"
    if level in ("minor", "unparsed"):
        return "fixed"
    return None
