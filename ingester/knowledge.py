"""Curated answer-cards distilled from research — WITH concrete figures.

Unlike raw laws (which the AI must summarize), these are pre-written, pre-enriched
cards holding the exact day-counts / rules / citations for the most common
questions, so Level-2 synthesis cites real numbers ("Κανονική: 7 εργ.") instead
of "βλ. τον οδηγό". source='knowledge'; inserted + enriched once.
"""
from __future__ import annotations

import json

from . import config

_FILE = config.ROOT / "ingester" / "knowledge_data.json"


def records() -> list[dict]:
    if not _FILE.exists():
        return []
    out: list[dict] = []
    for c in json.loads(_FILE.read_text(encoding="utf-8")):
        out.append({
            "id": c["id"], "source": "knowledge", "source_label": "Συγκεντρωτικός οδηγός",
            "title": c["title"], "summary": c.get("summary_ai", ""),
            "summary_ai": c.get("summary_ai", ""), "doc_type": "Οδηγός",
            "fek": None, "ada": None, "date": None,
            "official_url": c.get("source_url") or None,
            "source_url": c.get("source_url") or None,
            "categories": c.get("categories", []), "levels": ["Όλες / Γενικό"],
            "keywords": [], "articles": c.get("articles", []),
            "excerpts": c.get("excerpts", []), "status": c.get("status", "ΟΔΗΓΟΣ"),
        })
    return out
