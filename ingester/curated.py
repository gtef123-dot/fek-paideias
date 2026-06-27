"""Integration: turn the AI-curated catalog readings into registry records.

The hand-off / subagent curation produced, for each harvested candidate, a
reading {subject, categories, education_relevant, level, in_force_signal} stored
in curation_ledger.json. This module joins those readings with the candidate
metadata (candidates.json) and emits durable records for the published registry:

  * only EDUCATION-RELEVANT readings are published (off-topic excluded);
  * status: in_force/unclear → ΙΣΧΥΟΝ (the frontend already shows a "confirm at
    ΦΕΚ" disclaimer for in-force), superseded → ΚΑΤΑΡΓΗΘΕΝ (kept as history);
  * categories/level come from the grounded reading (not the bare-id title);
  * the descriptive `subject` becomes the title; the ΦΕΚ ref is the badge.

`records()` mirrors seed.py/knowledge.py so run.py can upsert them every run.
`python -m ingester.curated` does a one-shot integration (prime → upsert → export)
with NO network/enrichment — for the initial publish.
"""
from __future__ import annotations

import json

from . import config

_CANDIDATES = config.ROOT / "ingester" / "candidates.json"
_LEDGER = config.ROOT / "ingester" / "curation_ledger.json"

_STATUS = {"in_force": "ΙΣΧΥΟΝ", "unclear": "ΙΣΧΥΟΝ", "superseded": "ΚΑΤΑΡΓΗΘΕΝ"}
_LEVELS = {
    "Πρωτοβάθμια": ["Πρωτοβάθμια"],
    "Δευτεροβάθμια": ["Δευτεροβάθμια"],
    "Όλες": ["Όλες / Γενικό"],
}


def _candidates() -> dict:
    if not _CANDIDATES.exists():
        return {}
    data = json.loads(_CANDIDATES.read_text(encoding="utf-8")).get("candidates", [])
    return {c["id"]: c for c in data if c.get("id")}


def _readings() -> dict:
    if not _LEDGER.exists():
        return {}
    out = {}
    for it in json.loads(_LEDGER.read_text(encoding="utf-8")).get("items", []):
        r = (it.get("data") or {}).get("reading")
        if r:
            out[it["id"]] = r
    return out


def records() -> list[dict]:
    cands, reads = _candidates(), _readings()
    out: list[dict] = []
    for rid, r in reads.items():
        if not r.get("education_relevant"):
            continue
        c = cands.get(rid)
        if not c:
            continue
        fek = c.get("fek")
        subject = (r.get("subject") or c.get("title") or "").strip()
        cats = r.get("categories") or []
        out.append({
            "id": rid,
            "source": "catalog",
            "source_label": "ΦΕΚ (κατάλογος e-nomothesia)",
            "title": subject,
            "summary": (r.get("note") or subject),
            "doc_type": c.get("doc_type") or "Νομοθεσία",
            "fek": fek,
            "ada": None,
            "date": c.get("date"),
            "official_url": c.get("official_url") or (fek or {}).get("pdf_url"),
            "source_url": c.get("source_url"),
            "status": _STATUS.get(r.get("in_force_signal"), "ΙΣΧΥΟΝ"),
            "categories": cats,
            "levels": _LEVELS.get(r.get("level"), ["Όλες / Γενικό"]),
            "classified_by": "ai-curated",
        })
    return out


def main() -> int:
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    from . import store
    from .taxonomy import FALLBACK_CATEGORY

    recs = records()
    print(f"== integrate {len(recs)} AI-curated education records ==")
    conn = store.connect()
    known = store.prime(conn)
    print(f"-> {len(known)} already in registry")
    added = updated = 0
    for rec in recs:
        if not rec.get("categories"):
            rec["categories"] = [FALLBACK_CATEGORY]
        existed = rec["id"] in known
        inserted = store.upsert_static(conn, rec)
        known.add(rec["id"])
        if inserted:
            added += 1
        elif existed:
            updated += 1
    conn.commit()
    total = store.export(conn)
    conn.close()
    print(f"== done: {added} new, {updated} refreshed, {total} total in registry ==")
    print(f"   wrote {config.INDEX_OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
