"""Seed corpus: foundational, evergreen education-law documents that teachers
ask about year-round (άδειες, ωράριο, ΕΑΕ, καθηκοντολόγιο, κανονισμός…).

These are NOT in the daily feed window, so we always keep them in the DB. Each
carries a `status` (ΙΣΧΥΟΝ / ΚΑΤΑΡΓΗΘΕΝ / ΠΡΟΣΦΑΤΗ ΑΛΛΑΓΗ / ΟΔΗΓΟΣ) so the
assistant can tell in-force law from superseded. Researched + cited (June 2026).
"""
from __future__ import annotations

import json

from . import config

_SEED_FILE = config.ROOT / "ingester" / "seed_data.json"


def _doc_type(title: str) -> str:
    t = title or ""
    if t.startswith("Νόμος"):
        return "Νόμος"
    if "Π.Δ." in t or "Προεδρικό" in t:
        return "Προεδρικό Διάταγμα"
    if "Υ.Α." in t or "Υπουργική" in t:
        return "Υπουργική Απόφαση"
    if "Εγκύκλιος" in t:
        return "Εγκύκλιος"
    if "Οδηγ" in t:
        return "Οδηγός"
    return "Νομοθεσία"


def records() -> list[dict]:
    if not _SEED_FILE.exists():
        return []
    seeds = json.loads(_SEED_FILE.read_text(encoding="utf-8"))
    out: list[dict] = []
    for s in seeds:
        fn, fi, yr = s.get("fek_number"), s.get("fek_issue"), s.get("year")
        fek = None
        if fn and fi and yr:
            issue = str(fi).upper()
            fek = {
                "number": fn, "issue": issue,
                "group": config.ISSUE_GROUP.get(issue), "date": None,
                "label": f"ΦΕΚ {fn}/{issue}/{yr}",
                "pdf_url": config.fek_pdf_url(fn, issue, yr),
            }
        rid = f"seed:{fn}/{fi}/{yr}" if fek else "seed:" + (s.get("title", "")[:70])
        out.append({
            "id": rid, "source": "seed", "source_label": "Θεμελιώδης νόμος",
            "title": s.get("title", ""), "summary": s.get("topic", ""),
            "doc_type": _doc_type(s.get("title", "")),
            "fek": fek, "ada": s.get("ada"),
            "date": f"{yr}-01-01" if yr else None,
            "official_url": (fek["pdf_url"] if fek else None) or s.get("url"),
            "source_url": s.get("url"),
            "status": s.get("status", "ΙΣΧΥΟΝ"),
        })
    return out
