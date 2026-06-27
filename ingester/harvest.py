"""Bulk harvest of the FULL e-nomothesia education catalog → candidate pool.

The daily RSS only surfaces ~10 recent items per feed. To make sure no major
in-force law is missing, we crawl the paginated category listings (the whole
back-catalog) ONCE into a candidate pool. These are NOT auto-published — they
feed the grounded verifier (verify.py), which decides what is in force.

Deterministic + cheap (plain HTTP, no agents/LLM/credits). Crash-safe: each
listing page is a checkpoint.Ledger item flushed to disk the moment it's
crawled, so a credit-out / kill loses at most the current page, and a re-run
resumes from the next pending page. The merged pool is written atomically.

    python -m ingester.harvest --max-pages 3   # validation crawl (a few pages)
    python -m ingester.harvest                 # full crawl (all education cats)
    python -m ingester.harvest --report        # summary of candidates.json
    python -m ingester.harvest --reset         # start the crawl over
"""
from __future__ import annotations

import json
import re
import sys

from . import config
from .checkpoint import DONE, Ledger, atomic_write_json
from .net import get
from .sources import enomothesia

BASE = "https://www.e-nomothesia.gr"
# The education catalogs. "kat-ekpaideuse" is the superset; the others add
# level-specific items and let dedup tag βαθμίδα. (Same feeds as config, but the
# HTML catalog — not the truncated RSS.)
CATALOG_CATEGORIES = [
    "kat-ekpaideuse",
    "protobathmia-ekpaideuse",
    "deuterobathmia-ekpaideuse",
    "idiotike-ekpaideuse-phrontisteria",
]
HARVEST_LEDGER = config.ROOT / "ingester" / "harvest_ledger.json"
CANDIDATES_OUT = config.ROOT / "ingester" / "candidates.json"

_PAGE_RE = re.compile(r"[?&]page=(\d+)")


def _page_url(cat: str, page: int) -> str:
    return f"{BASE}/{cat}/?page={page}"


def _last_page(cat: str) -> int:
    """Read the highest page number from the category's pagination (the 'last'
    link). Out-of-range pages clamp instead of 404-ing, so we trust this bound."""
    try:
        html = get(f"{BASE}/{cat}/").text
    except Exception as exc:  # noqa: BLE001
        print(f"   [harvest] {cat}: index fetch failed ({exc})")
        return 0
    pages = [int(m) for m in _PAGE_RE.findall(html)]
    return max(pages) if pages else 1


def _extract(cat: str, html: str) -> list[dict]:
    """Pull (slug, title) law entries from one listing page, deduped by slug.

    Each entry appears twice (title link + a 'Περισσότερα' link with the same
    href); we keep the descriptive title (the one with the ΦΕΚ / longest text).
    """
    pat = re.compile(
        r'href="[^"]*?/' + re.escape(cat) + r'/([^"]+?\.html)"[^>]*>(.*?)</a>', re.S)
    by_slug: dict[str, str] = {}
    for slug, inner in pat.findall(html):
        text = re.sub(r"<[^>]+>", " ", inner)
        text = re.sub(r"\s+", " ", text).strip()
        if not text or text in ("Περισσότερα", "Read more"):
            continue
        # Prefer the title that carries the ΦΕΚ citation / is longer.
        cur = by_slug.get(slug, "")
        if ("ΦΕΚ" in text and "ΦΕΚ" not in cur) or len(text) > len(cur):
            by_slug[slug] = text
    return [{"slug": s, "title": t} for s, t in by_slug.items()]


def _candidate(cat: str, slug: str, title: str) -> dict:
    """Normalize a catalog entry into a candidate record (same shape/id space as
    the RSS source, so it dedups against what we already have)."""
    fek = enomothesia._parse_fek(title)
    rid = f"fek:{fek['label']}" if fek else f"enomcat:{slug}"
    return {
        "id": rid,
        "source": "e-nomothesia-catalog",
        "source_label": "e-nomothesia.gr (κατάλογος)",
        "title": title,
        "doc_type": enomothesia._doc_type(title),
        "fek": fek,
        "date": fek["date"] if fek else None,
        "official_url": (fek["pdf_url"] if fek else None),
        "source_url": f"{BASE}/{cat}/{slug}",
        "category_path": cat,
    }


def known_ids() -> set[str]:
    """Ids already in the published registry (so we can flag what's NEW)."""
    ids: set[str] = set()
    if config.INDEX_OUT.exists():
        try:
            for r in json.loads(config.INDEX_OUT.read_text(encoding="utf-8")).get("records", []):
                if r.get("id"):
                    ids.add(r["id"])
        except Exception:  # noqa: BLE001
            pass
    return ids


def _merge_and_write(led: Ledger) -> dict:
    """Collect candidates from every crawled page, dedup, write atomically."""
    by_id: dict[str, dict] = {}
    for it in led.items.values():
        for c in it.get("data", {}).get("candidates", []):
            by_id.setdefault(c["id"], c)
    known = known_ids()
    cands = list(by_id.values())
    new = [c for c in cands if c["id"] not in known]
    atomic_write_json(CANDIDATES_OUT, {
        "count": len(cands), "new_vs_registry": len(new),
        "candidates": cands,
    })
    return {"total": len(cands), "new": len(new)}


def crawl(max_pages: int | None = None, categories: list[str] | None = None) -> dict:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    categories = categories or CATALOG_CATEGORIES
    led = Ledger(HARVEST_LEDGER)

    # Enumerate every (category, page) up to each category's last page.
    print("== bulk harvest · e-nomothesia education catalog ==")
    for cat in categories:
        last = _last_page(cat)
        print(f"   {cat}: {last} pages")
        led.add_many((f"{cat}:p{p}", {"cat": cat, "page": p})
                     for p in range(1, last + 1))

    todo = led.pending_ids()
    if max_pages is not None:
        todo = todo[:max_pages]
    print(f"-> {len(todo)} pages to crawl this run "
          f"(settled already: {len(led.items) - len(led.pending_ids())})")

    # Early-stop guard per category: stop after 2 consecutive all-duplicate pages
    # (the site clamps out-of-range pages to the last one).
    seen_slugs: dict[str, set[str]] = {}
    dry: dict[str, int] = {}
    for item_id in todo:
        meta = led.get(item_id)["data"]
        cat, page = meta["cat"], meta["page"]
        if dry.get(cat, 0) >= 2:
            led.mark(item_id, DONE, data={**meta, "candidates": [], "skipped": "post-end"})
            continue
        led.bump_attempt(item_id)
        try:
            html = get(_page_url(cat, page)).text
        except Exception as exc:  # noqa: BLE001
            led.mark(item_id, "failed", data=meta, error=str(exc)[:120])
            continue
        entries = _extract(cat, html)
        cands = [_candidate(cat, e["slug"], e["title"]) for e in entries]
        s = seen_slugs.setdefault(cat, set())
        fresh = [c for c in cands if c["source_url"] not in s]
        for c in cands:
            s.add(c["source_url"])
        dry[cat] = 0 if fresh else dry.get(cat, 0) + 1
        led.mark(item_id, DONE, data={**meta, "candidates": cands})
        print(f"   {cat} p{page}: {len(cands)} entries ({len(fresh)} fresh)")

    stats = _merge_and_write(led)
    print(f"\n== candidates: {stats['total']} total, {stats['new']} NEW vs registry ==")
    print(f"   {CANDIDATES_OUT}")
    print(f"   ledger: {led.summary()}")
    return stats


def _report() -> int:
    if not CANDIDATES_OUT.exists():
        print("no candidates.json yet — run the crawl first.")
        return 1
    d = json.loads(CANDIDATES_OUT.read_text(encoding="utf-8"))
    cands = d.get("candidates", [])
    by_type: dict[str, int] = {}
    with_fek = 0
    for c in cands:
        by_type[c.get("doc_type", "?")] = by_type.get(c.get("doc_type", "?"), 0) + 1
        if c.get("fek"):
            with_fek += 1
    print(f"candidates: {d.get('count')} | NEW vs registry: {d.get('new_vs_registry')} "
          f"| with parseable ΦΕΚ: {with_fek}")
    for t, n in sorted(by_type.items(), key=lambda x: -x[1]):
        print(f"   {n:>4}  {t}")
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    argv = argv if argv is not None else sys.argv[1:]
    if "--reset" in argv:
        for p in (HARVEST_LEDGER,):
            if p.exists():
                p.unlink()
        print("harvest ledger reset.")
        return 0
    if "--report" in argv:
        return _report()
    max_pages = None
    for i, a in enumerate(argv):
        if a == "--max-pages" and i + 1 < len(argv):
            max_pages = int(argv[i + 1])
    crawl(max_pages=max_pages)
    return 0


if __name__ == "__main__":
    sys.exit(main())
