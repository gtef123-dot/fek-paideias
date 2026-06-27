"""Daily ingester entry point.

    python -m ingester.run

Pipeline:
  1. prime the DB from the committed records.json (durable memory)
  2. fetch e-nomothesia + Διαύγεια
  3. classify ONLY brand-new records (rules + optional Gemini for the misses)
  4. enrichment pass: for new ΦΕΚ, extract PDF text + LLM summary/keywords/
     articles/excerpts — ONCE per ΦΕΚ, then stored forever
  5. export records.json
"""
from __future__ import annotations

import sys

# Windows consoles default to cp1252 and choke on Greek output.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

from datetime import datetime, timezone

from . import classify, config, curated, enrich, events, knowledge, pdf, seed, store
from .sources import diavgeia, enomothesia
from .taxonomy import FALLBACK_CATEGORY


def main() -> int:
    print("== ΦΕΚ Παιδείας · daily ingest ==")

    conn = store.connect()
    known = store.prime(conn)
    print(f"-> {len(known)} records already in memory (sharded index + per-doc)")
    pruned = store.prune_legacy_diavgeia(conn)
    if pruned:
        print(f"-> pruned {pruned} legacy Διαύγεια noise records (non-substantive types)")

    print("-> e-nomothesia (νομοθεσία/ΦΕΚ)")
    raw = enomothesia.fetch()
    enom_n = len(raw)
    print("-> Διαύγεια (δομημένο OpenData: ΚΑΝΟΝΙΣΤΙΚΕΣ/εγκύκλιοι/νόμοι ανά φορέα)")
    dia = diavgeia.fetch()
    raw += dia["records"]
    print(f"-> {len(raw)} fetched ({enom_n} e-nomothesia + {len(dia['records'])} Διαύγεια)")

    # ── Classify brand-new records only (skip everything already processed) ──
    # Διαύγεια is now pre-filtered to substantive decision types at the source,
    # so we keep all of them (no post-hoc noise filter needed).
    new = 0
    for rec in raw:
        if rec["id"] in known:
            continue
        title, summary = rec.get("title", ""), rec.get("summary", "")
        cats, levels = classify.classify_rules(f"{title} {summary}")

        # Topic-relevance gate for Διαύγεια: decisionType removed act-TYPE noise,
        # but ΥΠΑΙΘΑ also issues ΑΘΛΗΤΙΣΜΟΣ/θρησκευμάτων regulatory acts (καράτε,
        # αιγίδες, σωματεία) — keep only those the education rules actually match.
        if rec["source"] == "diavgeia" and not cats:
            continue

        classified_by = "rules"
        if not cats:
            ai = classify.gemini_refine(title, summary)
            if ai:
                cats, classified_by = ai, "gemini"
        if not cats:
            cats, classified_by = [FALLBACK_CATEGORY], "fallback"

        rec.update(categories=cats, levels=levels, classified_by=classified_by)
        store.insert_new(conn, rec)
        known.add(rec["id"])
        new += 1
    conn.commit()

    # ── Seed corpus: foundational, year-round laws kept permanently ──
    seed_recs = seed.records()
    seeds_added = seeds_updated = 0
    for rec in seed_recs:
        existed = rec["id"] in known
        cats, levels = classify.classify_rules(f"{rec['title']} {rec.get('summary', '')}")
        rec.update(categories=cats or [FALLBACK_CATEGORY], levels=levels,
                   classified_by="rules")
        inserted = store.upsert_static(conn, rec)
        known.add(rec["id"])
        if inserted:
            seeds_added += 1
        elif existed:
            seeds_updated += 1
    if seeds_added or seeds_updated:
        print(f"-> seed laws: {seeds_added} added, {seeds_updated} refreshed")
    conn.commit()

    # ── Curated knowledge cards (pre-enriched, concrete figures) ──
    kn_added = kn_updated = 0
    for rec in knowledge.records():
        existed = rec["id"] in known
        rec.update(classified_by="curated")
        inserted = store.upsert_static(conn, rec, enriched=True)
        known.add(rec["id"])
        if inserted:
            kn_added += 1
        elif existed:
            kn_updated += 1
    if kn_added or kn_updated:
        print(f"-> knowledge cards: {kn_added} added, {kn_updated} refreshed")
    conn.commit()

    # ── AI-curated catalog (the bulk in-force corpus from the hand-off) ──
    from .taxonomy import FALLBACK_CATEGORY as _FB
    cur_added = cur_updated = 0
    for rec in curated.records():
        if not rec.get("categories"):
            rec["categories"] = [_FB]
        existed = rec["id"] in known
        inserted = store.upsert_static(conn, rec)
        known.add(rec["id"])
        if inserted:
            cur_added += 1
        elif existed:
            cur_updated += 1
    if cur_added or cur_updated:
        print(f"-> AI-curated catalog: {cur_added} added, {cur_updated} refreshed")
    conn.commit()

    # ── Event detection (#1): flag tracked laws that incoming acts amend/repeal ──
    seed_keys: dict = {}
    for rec in seed_recs:
        k = events.primary_law_key(rec["title"])  # the seed's OWN law number, not citations
        if k:
            seed_keys.setdefault(k, set()).add(rec["id"])
    affected_links = 0
    for rec in raw:
        hit = events.change_targets(f"{rec.get('title', '')} {rec.get('summary', '')}") & seed_keys.keys()
        if not hit:
            continue
        fek = rec.get("fek") or {}
        amendment = {
            "label": fek.get("label") or (("ΑΔΑ " + rec["ada"]) if rec.get("ada")
                                          else (rec.get("title") or "")[:50]),
            "url": rec.get("official_url") or rec.get("source_url"),
            "date": rec.get("date"),
        }
        for k in hit:
            for sid in seed_keys[k]:
                store.add_affected(conn, sid, amendment)
                affected_links += 1
    if affected_links:
        print(f"-> event detection: {affected_links} amendment link(s) flagged on tracked laws")
    conn.commit()

    # ── Enrichment pass: ΦΕΚ + Διαύγεια εγκύκλιοι (summary/keywords/articles) ──
    pending = store.unenriched_fek(conn, config.ENRICH_MAX_PER_RUN)
    print(f"-> enriching {len(pending)} docs (max {config.ENRICH_MAX_PER_RUN}/run)...")
    for rid, title, url in pending:
        store.bump_enrich_tried(conn, rid)   # count the attempt (stops bad-PDF retries)
        text = pdf.extract_text(url)
        enr = enrich.enrich(title, text) if text else None
        if enr:
            store.save_enrichment(conn, rid, enr)
    conn.commit()

    # ── Per-source health + shape canary (#4: catch silent partial failures) ──
    # e-nomothesia RSS ALWAYS returns ~40 items (4 feeds × 10, not date-filtered);
    # 0 means the source/parse broke (WAF/schema), not "no news". That's a shape
    # failure we must surface — the DB is full from persistence, so count alone lies.
    canary = diavgeia.canary()  # broad, no-org query: distinguishes "API broke" from "quiet window"
    problems = []
    if enom_n == 0:
        problems.append("e-nomothesia: 0 items — πιθανό WAF/αλλαγή schema")
    if not canary.get("ok"):
        problems.append(f"Διαύγεια canary απέτυχε (API/schema): {canary}")
    health = {
        "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "e_nomothesia_fetched": enom_n,
        "diavgeia_orgs_scanned": dia["orgs_scanned"],
        "diavgeia_orgs_with_results": dia["orgs_with_results"],
        "diavgeia_fetched": len(dia["records"]),
        "diavgeia_canary": canary,
        "new_this_run": new,
        "problems": problems,
    }
    total = store.export(conn, health=health)
    conn.close()

    print(f"== done: {new} new, {total} total, "
          f"{enrich.enrich_count()} enriched, {classify.gemini_call_count()} Gemini calls ==")
    for p in problems:
        print(f"!! ΠΡΟΣΟΧΗ — {p}")
    print(f"   wrote {config.JSON_OUT}")
    if total == 0:
        print("!! HEALTHCHECK FAILED: 0 records — sources may be down; not overwriting good data")
        return 1
    if enom_n == 0:  # the reliable shape canary
        print("!! HEALTHCHECK FAILED: e-nomothesia parse returned nothing (shape changed?)")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
