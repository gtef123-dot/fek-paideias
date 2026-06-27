"""Coverage audit (Phase 0): which education topics have an in-force cornerstone?

The single instrument that answers "do we have correct law for EVERY topic?".
For each taxonomy category it reports how many in-force, authoritative records
cover it — and, crucially, which categories have NONE (the gaps to fill).

A record "covers" a category as a CORNERSTONE when it:
  * is tagged with that category,
  * is not marked ΚΑΤΑΡΓΗΘΕΝ (superseded), and
  * carries real authority (primary_law / official_circular) — i.e. an actual
    ΦΕΚ-backed law/decree/circular, not a guide or a daily Διαύγεια act.

Read-only: consumes docs/data/index.json (produced by store.export). Run with

    python -m ingester.coverage          # human table
    python -m ingester.coverage --json    # machine-readable (for CI / frontend)
    python -m ingester.coverage --strict  # exit 1 if any category has 0 cornerstones
"""
from __future__ import annotations

import json
import sys

from . import config
from .taxonomy import CATEGORY_NAMES, FALLBACK_CATEGORY

# Authority levels that count as a real, citable cornerstone of a topic.
CORNERSTONE_LEVELS = {"primary_law", "official_circular"}
SUPERSEDED = "ΚΑΤΑΡΓΗΘΕΝ"


def _load_index() -> list[dict]:
    if not config.INDEX_OUT.exists():
        raise SystemExit(f"!! Δεν βρέθηκε {config.INDEX_OUT} — τρέξε πρώτα τον ingester.")
    data = json.loads(config.INDEX_OUT.read_text(encoding="utf-8"))
    return data.get("records", [])


def build_report(records: list[dict]) -> dict:
    """For every category: cornerstones, total tagged, and verification breakdown."""
    cats = [c for c in CATEGORY_NAMES] + [FALLBACK_CATEGORY]
    report: dict[str, dict] = {
        c: {"cornerstones": [], "tagged": 0, "superseded": 0,
            "verified": 0, "needs_primary": 0} for c in cats
    }

    for r in records:
        status = r.get("status") or ""
        level = r.get("authority_level") or "unknown"
        vstatus = r.get("verification_status") or "unverified"
        for c in r.get("categories", []):
            bucket = report.get(c)
            if bucket is None:
                continue
            bucket["tagged"] += 1
            if status == SUPERSEDED:
                bucket["superseded"] += 1
                continue
            if level in CORNERSTONE_LEVELS:
                bucket["cornerstones"].append({
                    "title": (r.get("title") or "")[:90],
                    "level": level, "status": status or "—",
                    "verification_status": vstatus,
                    "url": r.get("official_url") or r.get("source_url"),
                })
                if vstatus == "verified":
                    bucket["verified"] += 1
                elif vstatus in ("needs_primary_source", "partially_verified"):
                    bucket["needs_primary"] += 1
    return report


def gaps(report: dict) -> list[str]:
    """Categories with zero in-force cornerstones — the holes to fill."""
    return [c for c, b in report.items() if not b["cornerstones"]]


def _print_table(report: dict) -> None:
    covered = sum(1 for b in report.values() if b["cornerstones"])
    total = len(report)
    print(f"\n== ΠΙΝΑΚΑΣ ΚΑΛΥΨΗΣ — {covered}/{total} κατηγορίες με ισχύοντα θεμελιώδη νόμο ==\n")
    name_w = max(len(c) for c in report)
    print(f"{'ΚΑΤΗΓΟΡΙΑ':<{name_w}}  ΘΕΜΕΛ.  ✓ΕΠΑΛ  ΣΥΝΟΛΟ  ΚΑΤΑΡΓ.")
    print("-" * (name_w + 34))
    for c, b in report.items():
        n = len(b["cornerstones"])
        flag = "  " if n else "❌"
        print(f"{flag}{c:<{name_w-2}}  {n:>5}  {b['verified']:>5}  "
              f"{b['tagged']:>6}  {b['superseded']:>6}")

    holes = gaps(report)
    if holes:
        print(f"\n!! ΚΕΝΑ — {len(holes)} κατηγορίες ΧΩΡΙΣ ισχύοντα θεμελιώδη νόμο:")
        for c in holes:
            print(f"   • {c}")
    else:
        print("\n✓ Καμία κενή κατηγορία.")

    # Verification health across all cornerstones.
    allc = [cs for b in report.values() for cs in b["cornerstones"]]
    ver = sum(1 for cs in allc if cs["verification_status"] == "verified")
    print(f"\nΕπαλήθευση θεμελιωδών: {ver}/{len(allc)} 'verified' "
          f"({len(allc) - ver} χρειάζονται grounded έλεγχο)")


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    argv = argv if argv is not None else sys.argv[1:]
    report = build_report(_load_index())

    if "--json" in argv:
        print(json.dumps({
            "covered": sum(1 for b in report.values() if b["cornerstones"]),
            "total": len(report), "gaps": gaps(report),
            "by_category": {c: {"cornerstones": len(b["cornerstones"]),
                                "verified": b["verified"], "tagged": b["tagged"],
                                "superseded": b["superseded"]}
                            for c, b in report.items()},
        }, ensure_ascii=False, indent=1))
    else:
        _print_table(report)

    if "--strict" in argv and gaps(report):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
