"""Source authority — trust level, verification status, disclaimer need + ranking.

Derived (not stored as state) from source/doc_type/status, so it stays correct
even as those evolve. Drives result ranking and whether an answer may be stated
as settled law or must be flagged as "needs cross-check with a primary source".
"""
from __future__ import annotations

_PRIMARY_TYPES = {"Νόμος", "Προεδρικό Διάταγμα", "Πράξη Νομοθ. Περιεχομένου"}

# Ranking weight — higher ranks first (per the spec's order).
RANK = {
    "primary_law": 6, "official_circular": 5, "diavgeia_decision": 4,
    "official_guide": 3, "secondary_guide": 2, "unknown": 1,
}


def authority_level(source: str, doc_type: str, status: str) -> str:
    if (status or "") == "ΟΔΗΓΟΣ" or source == "knowledge":
        return "official_guide"
    if source == "diavgeia":
        return "diavgeia_decision"
    if (doc_type or "") in _PRIMARY_TYPES:
        return "primary_law"
    if (doc_type or "") == "Εγκύκλιος":
        return "official_circular"
    if source in ("e-nomothesia", "seed"):
        return "primary_law"   # ΥΑ/ΚΥΑ published in the official ΦΕΚ
    return "unknown"


def verification_status(level: str) -> str:
    return {
        "primary_law": "verified",
        "official_circular": "verified",
        "diavgeia_decision": "verified",
        "official_guide": "needs_primary_source",
        "secondary_guide": "unverified",
    }.get(level, "unverified")


def disclaimer_required(level: str) -> bool:
    return level in ("official_guide", "secondary_guide", "unknown")


def annotate(rec: dict) -> dict:
    """Attach authority_level / verification_status / legal_disclaimer_required."""
    lvl = authority_level(rec.get("source", ""), rec.get("doc_type", ""), rec.get("status") or "")
    rec["authority_level"] = lvl
    rec["verification_status"] = verification_status(lvl)
    rec["legal_disclaimer_required"] = disclaimer_required(lvl)
    return rec
