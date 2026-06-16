"""Central configuration: endpoints, identifiers, polite-crawler settings.

All sources here were verified live (June 2026). The et.gr backend is an
undocumented-but-public API, so we keep its details isolated here to make a
future change a one-file fix.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

# ── Paths ────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
SITE_DIR = ROOT / "docs"               # GitHub Pages serves main -> /docs
DB_PATH = DATA_DIR / "fek.db"          # local working DB (rebuilt from the sharded data)

# Sharded data (served + durable memory). The frontend loads only index.json and
# lazy-loads a per-document file on demand, so nothing grows unboundedly in memory.
SITE_DATA_DIR = SITE_DIR / "data"
INDEX_OUT = SITE_DATA_DIR / "index.json"          # data/index.json (lightweight)
DOCS_OUT_DIR = SITE_DATA_DIR / "docs"             # data/docs/{year}/{doc_id}.json
JSON_OUT = INDEX_OUT                               # back-compat alias (selftest etc.)

# ── Polite crawler ───────────────────────────────────────────────────────────
# e-nomothesia has a WAF that drops non-browser User-Agents, so we present a
# normal browser UA and add a short delay + retries everywhere.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
REQUEST_TIMEOUT = 30          # seconds
REQUEST_DELAY = 1.0           # seconds between requests to the same host
MAX_RETRIES = 3

# ── e-nomothesia.gr education RSS feeds (laws / decrees / ministerial acts) ───
# Each feed is education-scoped and embeds the ΦΕΚ number in the item title.
ENOMOTHESIA_FEEDS = [
    ("Εκπαίδευση (γενικά)", "https://www.e-nomothesia.gr/kat-ekpaideuse/rss.xml"),
    ("Πρωτοβάθμια", "https://www.e-nomothesia.gr/protobathmia-ekpaideuse/rss.xml"),
    ("Δευτεροβάθμια", "https://www.e-nomothesia.gr/deuterobathmia-ekpaideuse/rss.xml"),
    ("Ιδιωτική / Φροντιστήρια",
     "https://www.e-nomothesia.gr/idiotike-ekpaideuse-phrontisteria/rss.xml"),
]

# ── Διαύγεια OpenData API (circulars / decisions) ────────────────────────────
DIAVGEIA_BASE = "https://diavgeia.gov.gr/opendata"
# Υπουργείο Παιδείας, Θρησκευμάτων και Αθλητισμού
MINEDU_ORG_UID = "100081880"
# How many days back to scan each run (covers publish lag; dedup handles overlap)
DIAVGEIA_LOOKBACK_DAYS = 3
DIAVGEIA_PAGE_SIZE = 200


def _load_diavgeia_config() -> tuple[list[dict], bool]:
    """Config-driven org list (edit diavgeia_config.json, no code change).
    Returns (orgs, scan_all_directorates). With scan_all_directorates=true also
    loads the 248 ΔΠΕ/ΔΔΕ from diavgeia_orgs_reference.json (national coverage)."""
    cfg_dir = Path(__file__).resolve().parent
    default = [{"name": "ΥΠΑΙΘΑ", "org_id": MINEDU_ORG_UID}]
    f = cfg_dir / "diavgeia_config.json"
    if not f.exists():
        return default, False
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default, False
    orgs = [o for o in d.get("orgs", []) if o.get("org_id")] or list(default)
    scan_all = bool(d.get("scan_all_directorates"))
    if scan_all:
        ref = cfg_dir / "diavgeia_orgs_reference.json"
        try:
            seen = {o["org_id"] for o in orgs}
            for x in json.loads(ref.read_text(encoding="utf-8")).get("directorates", []):
                if x.get("org_id") and x["org_id"] not in seen:
                    orgs.append({"name": x.get("name", x["org_id"]), "org_id": x["org_id"]})
                    seen.add(x["org_id"])
        except Exception:  # noqa: BLE001
            pass
    return orgs, scan_all


DIAVGEIA_ORGS, DIAVGEIA_SCAN_ALL = _load_diavgeia_config()

# Substantive decision types to KEEP (whitelist) — cuts ~95% individual-act noise
# (διαπιστωτικές ΜΚ / αναλήψεις / ατομικοί διορισμοί, type 2.4.x) at the SOURCE.
# Verified live against types.json + sample subjects.
DIAVGEIA_SUBSTANTIVE_TYPES = ["Α.1.1", "Α.1.2", "Α.2", "Α.3", "Α.4"]

# Διαύγεια decision-type leaf codes -> friendly Greek label (verified via types.json).
DIAVGEIA_TYPES = {
    "Α.1.1": "Νόμος",
    "Α.1.2": "Πράξη Νομοθ. Περιεχομένου",
    "Α.2": "Κανονιστική Πράξη",
    "Α.3": "Εγκύκλιος",
    "Α.4": "Γνωμοδότηση",
}

# ── Εθνικό Τυπογραφείο / ΦΕΚ (et.gr) ─────────────────────────────────────────
ETGR_API_BASE = "https://searchetv99.azurewebsites.net/api"
# Deterministic public PDF blob storage (verified live; CORS-enabled).
FEK_BLOB_BASE = "https://ia37rg02wpsa01.blob.core.windows.net/fek"
# ΦΕΚ τεύχος (issue letter) -> IssueGroupID used in the blob path.
ISSUE_GROUP = {
    "Α": "01", "Β": "02", "Γ": "03", "Δ": "04",
    "ΑΕΙΔ": "05", "ΑΣΕΠ": "06", "ΔΔΣ": "07", "ΑΠΣ": "08",
    "ΥΟΔΔ": "09", "ΑΑΠ": "10", "ΝΠΔΔ": "11", "ΠΑΡΑΡΤΗΜΑ": "12",
    "ΔΕΒΙ": "13", "ΑΕ-ΕΠΕ": "14", "ΟΠΚ": "15",
}

# ── Gemini (optional AI refinement layer) ────────────────────────────────────
# Free tier; set GEMINI_API_KEY in the environment / GitHub Actions secret, or
# in a local `.env` file (gitignored — never commit it).
def _load_dotenv() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


_load_dotenv()
# If unset, the pipeline runs on the rule-based classifier alone.
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent"
)
# Hard cap on Gemini calls per run (shared by classification + enrichment).
GEMINI_MAX_CALLS_PER_RUN = 120

# ── Per-ΦΕΚ enrichment (one-time at ingest, then stored forever) ──────────────
# For each NEW ΦΕΚ we extract the PDF text once and ask the LLM for a summary,
# keywords, key articles and excerpts — stored in records.json and never redone.
ENRICH_MAX_PER_RUN = int(os.environ.get("ENRICH_MAX_PER_RUN", "40"))
PDF_MAX_CHARS = 30000  # extracted ΦΕΚ text fed to the LLM (covers more of big laws)


def fek_pdf_url(number: int, issue_letter: str, year: int) -> str | None:
    """Build the official ΦΕΚ PDF URL from (number, τεύχος, year)."""
    grp = ISSUE_GROUP.get(issue_letter.upper())
    if not grp:
        return None
    return f"{FEK_BLOB_BASE}/{grp}/{year}/{year}{grp}{number:05d}.pdf"
