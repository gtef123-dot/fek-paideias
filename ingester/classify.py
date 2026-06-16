"""Two-layer classifier + shared Gemini helper.

Layer 1 (always on): deterministic Greek keyword rules — free, instant, auditable.
Layer 2 (optional): Google Gemini Flash free tier — invoked ONLY for kept items
the rules could not categorize (run.py gates this) and for one-time enrichment.
Rate-limited to stay inside the free tier; disabled when GEMINI_API_KEY is unset.

SECURITY: never print exception details from a Gemini HTTP call — the request URL
carries the API key as a query param. We log only the error type.
"""
from __future__ import annotations

import json
import time

import requests

from . import config
from .taxonomy import CATEGORY_NAMES, classify_categories, classify_levels, normalize

_gemini_calls = 0
_last_call_ts = 0.0
_MIN_GAP = 4.5  # seconds between Gemini calls (~13/min, under the ~15 RPM free cap)


def classify_rules(text: str) -> tuple[list[str], list[str]]:
    """Return (categories, levels) from the deterministic rule engine."""
    return classify_categories(text), classify_levels(normalize(text))


def _respect_rate_limit() -> None:
    global _last_call_ts
    wait = _MIN_GAP - (time.monotonic() - _last_call_ts)
    if wait > 0:
        time.sleep(wait)
    _last_call_ts = time.monotonic()


def gemini_json(prompt: str):
    """Rate-limited Gemini call returning parsed JSON (dict/list), or None."""
    global _gemini_calls
    if not config.GEMINI_API_KEY or _gemini_calls >= config.GEMINI_MAX_CALLS_PER_RUN:
        return None
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
    }
    for _attempt in range(2):
        _respect_rate_limit()
        try:
            _gemini_calls += 1
            r = requests.post(
                config.GEMINI_URL,
                params={"key": config.GEMINI_API_KEY},
                json=body,
                timeout=config.REQUEST_TIMEOUT,
            )
            if r.status_code == 429:
                print("   [gemini] 429 rate-limited; backing off 30s")
                time.sleep(30)
                continue
            r.raise_for_status()
            raw = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            return json.loads(raw)
        except Exception as exc:  # noqa: BLE001 - never leak the key via exc text
            print(f"   [gemini] skipped ({type(exc).__name__})")
            return None
    return None


def gemini_refine(title: str, summary: str) -> list[str] | None:
    """Ask Gemini to pick categories for an item the rules missed."""
    cat_list = "\n".join(f"- {c}" for c in CATEGORY_NAMES)
    prompt = (
        "Είσαι ταξινομητής ελληνικής εκπαιδευτικής νομοθεσίας (ΦΕΚ/εγκύκλιοι). "
        "Διάλεξε 1 έως 3 κατηγορίες ΑΠΟΚΛΕΙΣΤΙΚΑ από την παρακάτω λίστα που "
        "ταιριάζουν στο κείμενο. Αν καμία δεν ταιριάζει, επίστρεψε άδεια λίστα.\n\n"
        f"ΚΑΤΗΓΟΡΙΕΣ:\n{cat_list}\n\n"
        f"ΤΙΤΛΟΣ: {title}\nΠΕΡΙΛΗΨΗ: {summary}\n\n"
        'Απάντησε ΜΟΝΟ με JSON της μορφής {"categories": ["..."]}.'
    )
    data = gemini_json(prompt)
    if not isinstance(data, dict):
        return None
    return [c for c in data.get("categories", []) if c in CATEGORY_NAMES][:3]


def gemini_call_count() -> int:
    return _gemini_calls
