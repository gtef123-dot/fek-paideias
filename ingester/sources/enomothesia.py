"""e-nomothesia.gr education RSS feeds -> normalized records.

Item title format examples:
    "Υπουργική Απόφαση 75707/Δ2/2026 - ΦΕΚ 3358/Β/12-6-2026"
    "Προεδρικό Διάταγμα 28/2026 - ΦΕΚ 75/Α/18-5-2026"
The ΦΕΚ citation in the title lets us build the official PDF URL directly.

NOTE: we index feed METADATA (title, ΦΕΚ number, date, short summary) and link
back to e-nomothesia and to the official ΦΕΚ PDF — we do not republish bodies.
"""
from __future__ import annotations

import re

import feedparser

from .. import config
from ..net import get

# ΦΕΚ 3358/Β/12-6-2026  ->  number, issue letter, dd, mm, yyyy
_FEK_RE = re.compile(r"ΦΕΚ\s*([0-9]+)\s*/\s*([Α-Ωα-ω]+)\s*/\s*(\d{1,2})-(\d{1,2})-(\d{4})")

_DOCTYPE_PREFIXES = [
    ("Κοινή Υπουργική Απόφαση", "ΚΥΑ"),
    ("Υπουργική Απόφαση", "Υπουργική Απόφαση"),
    ("Προεδρικό Διάταγμα", "Προεδρικό Διάταγμα"),
    ("Πράξη Νομοθετικού Περιεχομένου", "ΠΝΠ"),
    ("Νόμος", "Νόμος"),
    ("Εγκύκλιος", "Εγκύκλιος"),
    ("Απόφαση", "Απόφαση"),
]


def _doc_type(title: str) -> str:
    for prefix, label in _DOCTYPE_PREFIXES:
        if title.strip().startswith(prefix):
            return label
    return "Νομοθεσία"


def _parse_fek(title: str) -> dict | None:
    m = _FEK_RE.search(title)
    if not m:
        return None
    number = int(m.group(1))
    issue = m.group(2).upper()
    dd, mm, yyyy = int(m.group(3)), int(m.group(4)), int(m.group(5))
    return {
        "number": number,
        "issue": issue,
        "group": config.ISSUE_GROUP.get(issue),
        "date": f"{yyyy:04d}-{mm:02d}-{dd:02d}",
        "label": f"ΦΕΚ {number}/{issue}/{dd}-{mm}-{yyyy}",
        "pdf_url": config.fek_pdf_url(number, issue, yyyy),
    }


def _clean(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html or "")
    return re.sub(r"\s+", " ", text).strip()


def fetch() -> list[dict]:
    records: list[dict] = []
    seen: set[str] = set()

    for feed_label, url in config.ENOMOTHESIA_FEEDS:
        try:
            resp = get(url)
        except Exception as exc:  # noqa: BLE001
            print(f"   [enomothesia] feed failed: {feed_label} ({exc})")
            continue

        parsed = feedparser.parse(resp.content)
        print(f"   [enomothesia] {feed_label}: {len(parsed.entries)} items")

        for entry in parsed.entries:
            title = (entry.get("title") or "").strip()
            link = (entry.get("link") or "").strip()
            fek = _parse_fek(title)
            # Stable id: prefer the ΦΕΚ label, else the source link.
            rid = f"fek:{fek['label']}" if fek else f"enom:{link}"
            if rid in seen:
                continue
            seen.add(rid)

            records.append({
                "id": rid,
                "source": "e-nomothesia",
                "source_label": "e-nomothesia.gr",
                "title": title,
                "summary": _clean(entry.get("description", "")),
                "doc_type": _doc_type(title),
                "fek": fek,
                "ada": None,
                "date": fek["date"] if fek else _entry_date(entry),
                "official_url": fek["pdf_url"] if fek else None,
                "source_url": link,
            })
    return records


def _entry_date(entry) -> str | None:
    t = entry.get("published_parsed") or entry.get("updated_parsed")
    if t:
        return f"{t.tm_year:04d}-{t.tm_mon:02d}-{t.tm_mday:02d}"
    return None
