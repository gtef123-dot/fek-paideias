"""SQLite working store + SHARDED JSON output (durable memory + frontend view).

Durable memory = the sharded files under docs/data/: a lightweight `index.json`
(all docs, search/display/ranking fields only) + one `docs/{year}/{doc_id}.json`
per document (full content). On every run we prime the (ephemeral) SQLite DB
from them, so history persists and each ΦΕΚ is processed/enriched exactly once.
The frontend loads only index.json and lazy-loads a per-doc file on demand —
nothing grows unboundedly in browser memory.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone

from . import authority, config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    id            TEXT PRIMARY KEY,
    source        TEXT,
    source_label  TEXT,
    title         TEXT,
    summary       TEXT,
    doc_type      TEXT,
    fek_label     TEXT,
    fek_number    INTEGER,
    fek_issue     TEXT,
    date          TEXT,
    ada           TEXT,
    categories    TEXT,   -- JSON array
    levels        TEXT,   -- JSON array
    classified_by TEXT,
    official_url  TEXT,
    source_url    TEXT,
    added_at      TEXT,
    summary_ai    TEXT,
    keywords      TEXT,   -- JSON array
    articles      TEXT,   -- JSON array
    excerpts      TEXT,   -- JSON array
    enriched      INTEGER DEFAULT 0,
    enrich_tried  INTEGER DEFAULT 0,   -- attempts so far (stop retrying bad PDFs)
    status        TEXT,    -- ΙΣΧΥΟΝ / ΚΑΤΑΡΓΗΘΕΝ / ΠΡΟΣΦΑΤΗ ΑΛΛΑΓΗ / ΟΔΗΓΟΣ (seeds)
    affected_by   TEXT     -- JSON: incoming acts that amend/repeal this tracked law
);
CREATE INDEX IF NOT EXISTS idx_date ON records(date);
"""


# Columns added after the original schema — applied to pre-existing DBs so a
# persisted fek.db keeps working across upgrades (CREATE IF NOT EXISTS won't add
# columns to an existing table).
_MIGRATIONS = {
    "summary_ai": "TEXT", "keywords": "TEXT", "articles": "TEXT", "excerpts": "TEXT",
    "enriched": "INTEGER DEFAULT 0", "status": "TEXT", "enrich_tried": "INTEGER DEFAULT 0",
    "affected_by": "TEXT",
}


def connect() -> sqlite3.Connection:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    conn.executescript(_SCHEMA)
    have = {r[1] for r in conn.execute("PRAGMA table_info(records)")}
    for col, ddl in _MIGRATIONS.items():
        if col not in have:
            conn.execute(f"ALTER TABLE records ADD COLUMN {col} {ddl}")
    conn.commit()
    return conn


def _arr(x) -> str:
    return json.dumps(x or [], ensure_ascii=False)


def _doc_id(rid: str) -> str:
    """Filesystem/URL-safe stable id for a record (record ids contain /, spaces, Greek)."""
    return hashlib.sha1((rid or "").encode("utf-8")).hexdigest()[:16]


def _year_of(date: str | None) -> str:
    return (date or "")[:4] if (date or "")[:4].isdigit() else "ref"


def _doc_path(doc_id: str, year: str):
    return config.DOCS_OUT_DIR / year / f"{doc_id}.json"


def _read_doc(entry: dict) -> dict | None:
    doc_id = entry.get("doc_id") or _doc_id(entry.get("id", ""))
    p = _doc_path(doc_id, entry.get("year") or _year_of(entry.get("date")))
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return None
    return None


def prime(conn: sqlite3.Connection) -> set[str]:
    """Rebuild the DB from the sharded index + per-doc files. Returns known ids."""
    ids: set[str] = set()
    if not config.INDEX_OUT.exists():
        return ids
    try:
        idx = json.loads(config.INDEX_OUT.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return ids
    for entry in idx.get("records", []):
        rid = entry.get("id")
        if not rid:
            continue
        r = {**entry, **(_read_doc(entry) or {})}  # per-doc (full) overrides light fields
        ids.add(rid)
        conn.execute(
            """INSERT OR IGNORE INTO records
               (id, source, source_label, title, summary, doc_type, fek_label,
                date, ada, categories, levels, classified_by, official_url,
                source_url, added_at, summary_ai, keywords, articles, excerpts,
                enriched, status, enrich_tried, affected_by)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                rid, r.get("source"), r.get("source_label"), r.get("title"),
                r.get("summary"), r.get("doc_type"), r.get("fek_label"),
                r.get("date"), r.get("ada"), _arr(r.get("categories")),
                _arr(r.get("levels")), r.get("classified_by"), r.get("official_url"),
                r.get("source_url"), r.get("added_at"), r.get("summary_ai"),
                _arr(r.get("keywords")), _arr(r.get("articles")),
                _arr(r.get("excerpts")), 1 if r.get("enriched") else 0, r.get("status"),
                int(r.get("enrich_tried") or 0), _arr(r.get("affected_by")),
            ),
        )
    conn.commit()
    return ids


def insert_new(conn: sqlite3.Connection, rec: dict) -> None:
    """Insert a brand-new classified record (enrichment happens in a later pass)."""
    fek = rec.get("fek") or {}
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    conn.execute(
        """INSERT OR IGNORE INTO records
           (id, source, source_label, title, summary, doc_type, fek_label,
            fek_number, fek_issue, date, ada, categories, levels, classified_by,
            official_url, source_url, added_at, status, enriched)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)""",
        (
            rec["id"], rec.get("source"), rec.get("source_label"), rec.get("title"),
            rec.get("summary"), rec.get("doc_type"), fek.get("label"),
            fek.get("number"), fek.get("issue"), rec.get("date"), rec.get("ada"),
            _arr(rec.get("categories")), _arr(rec.get("levels")),
            rec.get("classified_by"), rec.get("official_url"), rec.get("source_url"),
            today, rec.get("status"),
        ),
    )


def upsert_static(conn: sqlite3.Connection, rec: dict, *, enriched: bool = False) -> bool:
    """Insert or update a seed/knowledge record. Returns True when newly inserted."""
    exists = conn.execute("SELECT 1 FROM records WHERE id=?", (rec["id"],)).fetchone()
    insert_new(conn, rec)

    fek = rec.get("fek") or {}
    conn.execute(
        """UPDATE records SET source=?, source_label=?, title=?, summary=?,
           doc_type=?, fek_label=?, fek_number=?, fek_issue=?, date=?, ada=?,
           categories=?, levels=?, classified_by=?, official_url=?, source_url=?,
           status=?
           WHERE id=?""",
        (
            rec.get("source"), rec.get("source_label"), rec.get("title"),
            rec.get("summary"), rec.get("doc_type"), fek.get("label"),
            fek.get("number"), fek.get("issue"), rec.get("date"), rec.get("ada"),
            _arr(rec.get("categories")), _arr(rec.get("levels")),
            rec.get("classified_by"), rec.get("official_url"),
            rec.get("source_url"), rec.get("status"), rec["id"],
        ),
    )
    if enriched:
        save_enrichment(conn, rec["id"], {
            "summary_ai": rec.get("summary_ai", ""),
            "keywords": rec.get("keywords", []),
            "articles": rec.get("articles", []),
            "excerpts": rec.get("excerpts", []),
        })
    return exists is None


def unenriched_fek(conn: sqlite3.Connection, limit: int) -> list[tuple]:
    """Documents with a PDF not yet enriched — ΦΕΚ (e-nomothesia/seed) AND the
    substantive Διαύγεια acts (εγκύκλιοι/νόμοι/ΠΝΠ), which teachers care about.
    `enrich_tried < 2` stops us from re-fetching permanently-bad/scanned PDFs."""
    return conn.execute(
        """SELECT id, title, official_url FROM records
           WHERE enriched = 0 AND enrich_tried < 2 AND official_url IS NOT NULL
             AND ( (source IN ('e-nomothesia','seed') AND official_url LIKE 'http%fek/%')
                OR source='diavgeia' )   -- Διαύγεια is pre-filtered to substantive types
           ORDER BY (source='diavgeia') ASC, date DESC LIMIT ?""",
        (limit,),
    ).fetchall()


def bump_enrich_tried(conn: sqlite3.Connection, rid: str) -> None:
    conn.execute("UPDATE records SET enrich_tried = enrich_tried + 1 WHERE id=?", (rid,))


def add_affected(conn: sqlite3.Connection, rid: str, amendment: dict) -> None:
    """Record (dedup by label) that an incoming act amends/repeals tracked law `rid`."""
    row = conn.execute("SELECT affected_by FROM records WHERE id=?", (rid,)).fetchone()
    if row is None:
        return
    cur = json.loads(row[0]) if row[0] else []
    if any(a.get("label") == amendment.get("label") for a in cur):
        return
    cur.append(amendment)
    conn.execute("UPDATE records SET affected_by=? WHERE id=?", (_arr(cur), rid))


def prune_legacy_diavgeia(conn: sqlite3.Connection) -> int:
    """Clean Διαύγεια noise (idempotent): (a) non-substantive types (legacy
    individual acts before the decisionType whitelist); (b) substantive but
    OFF-TOPIC acts that only got the fallback category (ΥΠΑΙΘΑ sports/religion)."""
    from .taxonomy import FALLBACK_CATEGORY
    keep = list(config.DIAVGEIA_TYPES.values())
    ph = ",".join("?" * len(keep))
    n = conn.execute(
        f"DELETE FROM records WHERE source='diavgeia' AND doc_type NOT IN ({ph})", keep).rowcount
    n += conn.execute(
        "DELETE FROM records WHERE source='diavgeia' AND categories=?",
        (json.dumps([FALLBACK_CATEGORY], ensure_ascii=False),)).rowcount
    return n


def save_enrichment(conn: sqlite3.Connection, rid: str, enr: dict) -> None:
    conn.execute(
        """UPDATE records SET summary_ai=?, keywords=?, articles=?, excerpts=?,
           enriched=1 WHERE id=?""",
        (enr.get("summary_ai"), _arr(enr.get("keywords")), _arr(enr.get("articles")),
         _arr(enr.get("excerpts")), rid),
    )


def export(conn: sqlite3.Connection, health: dict | None = None) -> int:
    """Write the SHARDED output: a lightweight index.json (all docs) + one full
    per-document JSON each (docs/{year}/{doc_id}.json). Returns the doc count."""
    rows = conn.execute(
        """SELECT id, source, source_label, title, summary, doc_type, fek_label,
                  date, ada, categories, levels, classified_by, official_url,
                  source_url, added_at, summary_ai, keywords, articles, excerpts,
                  enriched, status, enrich_tried, affected_by
           FROM records ORDER BY date DESC, added_at DESC"""
    ).fetchall()

    config.DOCS_OUT_DIR.mkdir(parents=True, exist_ok=True)
    index_records, current_files = [], set()
    cat_counts: dict[str, int] = {}
    level_counts: dict[str, int] = {}
    type_counts: dict[str, int] = {}
    enriched_n = 0

    for r in rows:
        cats = json.loads(r[9] or "[]")
        levels = json.loads(r[10] or "[]")
        full = {
            "id": r[0], "source": r[1], "source_label": r[2], "title": r[3],
            "summary": r[4], "doc_type": r[5], "fek_label": r[6], "date": r[7],
            "ada": r[8], "categories": cats, "levels": levels,
            "classified_by": r[11], "official_url": r[12], "source_url": r[13],
            "added_at": r[14], "summary_ai": r[15],
            "keywords": json.loads(r[16] or "[]"),
            "articles": json.loads(r[17] or "[]"),
            "excerpts": json.loads(r[18] or "[]"),
            "enriched": bool(r[19]), "status": r[20], "enrich_tried": r[21] or 0,
            "affected_by": json.loads(r[22]) if r[22] else [],
        }
        authority.annotate(full)
        doc_id = _doc_id(full["id"])
        year = _year_of(full["date"])
        full["doc_id"], full["year"] = doc_id, year

        # Full per-document file (deterministic content → no git churn if unchanged).
        p = _doc_path(doc_id, year)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(full, ensure_ascii=False, indent=1), encoding="utf-8")
        current_files.add(p.resolve())

        # Lightweight index entry (search/display/ranking fields only).
        disp = (full["summary_ai"] or full["summary"] or "").strip()
        index_records.append({
            "doc_id": doc_id, "id": full["id"], "year": year, "source": full["source"],
            "source_label": full["source_label"], "title": full["title"],
            "summary": disp[:280], "keywords": full["keywords"][:8],
            "doc_type": full["doc_type"], "fek_label": full["fek_label"],
            "ada": full["ada"], "date": full["date"], "categories": cats, "levels": levels,
            "status": full["status"], "enriched": full["enriched"],
            "authority_level": full["authority_level"],
            "verification_status": full["verification_status"],
            "legal_disclaimer_required": full["legal_disclaimer_required"],
            "official_url": full["official_url"], "source_url": full["source_url"],
            "affected_by": full["affected_by"],
        })
        if full["enriched"]:
            enriched_n += 1
        for c in cats:
            cat_counts[c] = cat_counts.get(c, 0) + 1
        for lv in levels:
            level_counts[lv] = level_counts.get(lv, 0) + 1
        if r[5]:
            type_counts[r[5]] = type_counts.get(r[5], 0) + 1

    # Remove orphaned per-doc files (e.g. pruned records).
    for f in config.DOCS_OUT_DIR.rglob("*.json"):
        if f.resolve() not in current_files:
            try:
                f.unlink()
            except OSError:
                pass

    payload = {
        "meta": {
            "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "count": len(index_records),
            "enriched": enriched_n,
            "health": health or {},
            "categories": sorted(cat_counts.items(), key=lambda x: -x[1]),
            "levels": sorted(level_counts.items(), key=lambda x: -x[1]),
            "doc_types": sorted(type_counts.items(), key=lambda x: -x[1]),
        },
        "records": index_records,
    }
    config.SITE_DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.INDEX_OUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return len(index_records)
