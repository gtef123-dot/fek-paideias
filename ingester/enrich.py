"""One-time per-ΦΕΚ enrichment via Gemini.

For each NEW ΦΕΚ we extract — ONCE — a summary, keywords, key articles and short
excerpts, then store them. The teacher's queries (Level 1) and on-demand
synthesis (Level 2) read these stored fields; the PDF is never re-read and the
ΦΕΚ is never re-summarized.
"""
from __future__ import annotations

from . import classify, config

_enrich_calls = 0

# Note: literal { } in the JSON skeleton, so we use .replace (not str.format).
_PROMPT = """Αναλύεις ένα ΦΕΚ/έγγραφο ελληνικής εκπαιδευτικής νομοθεσίας.
Με βάση ΜΟΝΟ το παρακάτω κείμενο, επίστρεψε ΑΥΣΤΗΡΑ JSON αυτής της μορφής:
{
 "summary": "περίληψη 2-3 προτάσεων στα ελληνικά",
 "keywords": ["5-8 σύντομες λέξεις-κλειδιά"],
 "articles": ["σημαντικά άρθρα/σημεία, π.χ. 'Άρθρο 3: ...' (έως 5)"],
 "excerpts": ["1-3 σύντομα αποσπάσματα αυτούσια από το κείμενο"]
}

ΤΙΤΛΟΣ: <<TITLE>>

ΚΕΙΜΕΝΟ:
<<BODY>>"""


def enrich(title: str, full_text: str) -> dict | None:
    """Return the enrichment dict for one ΦΕΚ, or None if unavailable/capped."""
    global _enrich_calls
    if not config.GEMINI_API_KEY or _enrich_calls >= config.ENRICH_MAX_PER_RUN:
        return None
    body = (full_text or "")[: config.PDF_MAX_CHARS]
    if len(body) < 40:
        return None

    _enrich_calls += 1
    prompt = _PROMPT.replace("<<TITLE>>", title or "").replace("<<BODY>>", body)
    data = classify.gemini_json(prompt)
    if not isinstance(data, dict):
        return None
    return {
        "summary_ai": str(data.get("summary") or "").strip(),
        "keywords": [str(k).strip() for k in data.get("keywords", []) if str(k).strip()][:8],
        "articles": [str(a).strip() for a in data.get("articles", []) if str(a).strip()][:8],
        "excerpts": [str(e).strip() for e in data.get("excerpts", []) if str(e).strip()][:3],
    }


def enrich_count() -> int:
    return _enrich_calls
