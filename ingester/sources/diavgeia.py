"""Διαύγεια OpenData API -> normalized records (structured, not scraped).

We query the OpenData advanced search per organization, filtering by
`decisionTypeUid` to a WHITELIST of substantive types (Νόμος / ΠΝΠ / Κανονιστική
Πράξη / Εγκύκλιος / Γνωμοδότηση). This cuts ~95% of the individual-act noise
(διαπιστωτικές ΜΚ, αναλήψεις, ατομικοί διορισμοί) AT THE SOURCE — no keyword
scraping, no post-hoc heuristics.

Organizations come from diavgeia_config.json (Υπουργείο + ΙΕΠ by default; set
scan_all_directorates=true to add all 248 ΔΠΕ/ΔΔΕ for national coverage).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .. import config
from ..net import get

_TYPE_CLAUSE = "(" + " OR ".join(
    f'decisionTypeUid:"{t}"' for t in config.DIAVGEIA_SUBSTANTIVE_TYPES) + ")"


def _iso_from_ms(ms) -> str | None:
    if not ms:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def _fek_from_extra(extra: dict) -> dict | None:
    fek = (extra or {}).get("fek") or {}
    number, issue, year = fek.get("number"), fek.get("issue") or fek.get("issueGroup"), fek.get("year")
    if not (number and issue and year):
        return None
    try:
        number, year = int(number), int(year)
    except (TypeError, ValueError):
        return None
    issue = str(issue).upper()
    return {
        "number": number, "issue": issue, "group": config.ISSUE_GROUP.get(issue),
        "date": None, "label": f"ΦΕΚ {number}/{issue}/{year}",
        "pdf_url": config.fek_pdf_url(number, issue, year),
    }


def _record(d: dict, source_label: str) -> dict | None:
    ada = d.get("ada")
    if not ada:
        return None
    type_id = d.get("decisionTypeId", "")
    return {
        "id": f"ada:{ada}", "source": "diavgeia", "source_label": source_label,
        "title": (d.get("subject") or "").strip(),
        "summary": (d.get("subject") or "").strip(),
        "doc_type": config.DIAVGEIA_TYPES.get(type_id, "Απόφαση"),
        "doc_type_id": type_id, "fek": _fek_from_extra(d.get("extraFieldValues", {})),
        "ada": ada, "date": _iso_from_ms(d.get("issueDate")),
        "official_url": d.get("documentUrl"),
        "source_url": f"https://diavgeia.gov.gr/decision/view/{ada}",
    }


def _fetch_org(org: dict, start, end, seen: set, out: list) -> int:
    """Fetch substantive decisions for one org. Returns how many it added."""
    org_id, name = org["org_id"], org.get("name", org["org_id"])
    q = (f'organizationUid:"{org_id}" AND {_TYPE_CLAUSE} AND '
         f"issueDate:[DT({start}T00:00:00) TO DT({end}T23:59:59)]")
    page, total, added = 0, None, 0
    while True:
        params = {"q": q, "size": config.DIAVGEIA_PAGE_SIZE, "page": page, "sort": "recent"}
        try:
            payload = get(f"{config.DIAVGEIA_BASE}/search/advanced",
                          params=params, headers={"Accept": "application/json"}).json()
        except Exception as exc:  # noqa: BLE001
            print(f"   [diavgeia] {name} page {page} failed ({exc})")
            return added
        decisions = payload.get("decisions", [])
        if total is None:
            total = payload.get("info", {}).get("total", 0)
        for d in decisions:
            rec = _record(d, f"Διαύγεια · {name}")
            if rec and rec["ada"] not in seen:
                seen.add(rec["ada"])
                out.append(rec)
                added += 1
        page += 1
        if not decisions or (total and page * config.DIAVGEIA_PAGE_SIZE >= total):
            return added


def canary() -> dict:
    """Broad query (NO org filter) that must always return many results if the
    Διαύγεια API is alive — independent of which orgs we scan. Also a SHAPE check:
    a decision must still carry the fields we depend on (catches schema drift)."""
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=2)
    q = f"issueDate:[DT({start}T00:00:00) TO DT({end}T23:59:59)]"
    try:
        payload = get(f"{config.DIAVGEIA_BASE}/search/advanced",
                      params={"q": q, "size": 1, "sort": "recent"},
                      headers={"Accept": "application/json"}).json()
        total = payload.get("info", {}).get("total", 0)
        dec = payload.get("decisions", [])
        shape_ok = bool(dec) and all(
            k in dec[0] for k in ("ada", "subject", "issueDate", "decisionTypeId"))
        return {"ok": bool(total) and shape_ok, "total": total, "shape_ok": shape_ok}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": type(exc).__name__}


def fetch(lookback_days: int | None = None) -> dict:
    """Returns {'records': [...], 'orgs_scanned': n, 'orgs_with_results': n}."""
    lookback = lookback_days or config.DIAVGEIA_LOOKBACK_DAYS
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=lookback)

    out: list[dict] = []
    seen: set[str] = set()
    with_results = 0
    for org in config.DIAVGEIA_ORGS:
        if _fetch_org(org, start, end, seen, out) > 0:
            with_results += 1
    print(f"   [diavgeia] {len(out)} substantive records from "
          f"{len(config.DIAVGEIA_ORGS)} orgs ({with_results} with results)")
    return {"records": out, "orgs_scanned": len(config.DIAVGEIA_ORGS),
            "orgs_with_results": with_results}
