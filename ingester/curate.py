"""AI reading layer: read each candidate's ACTUAL text → topic / relevance / currency.

The catalog gives only bare-id titles ("Νόμος 5266/2026 - ΦΕΚ 4/Α/..."), so the
rule classifier can't tell what a candidate is about. This pass fetches the real
text and asks the AI (Gemini, grounded on that text — never from memory) for:
  - subject:            one-line description of what the act regulates
  - categories:         1–3 of the 21 taxonomy topics (or [] if none fit)
  - education_relevant: does it actually concern Α/θμια / Β/θμια education?
  - level:              Πρωτοβάθμια / Δευτεροβάθμια / Όλες
  - in_force_signal:    in_force / superseded / unclear (from the text)

Text source: the official ΦΕΚ PDF (blob, ~2000+); for pre-2000 acts the blob has
no extractable text, so we fall back to the e-nomothesia .html detail page (which
carries the consolidated "όπως ισχύει" body).

Checkpointed through checkpoint.Ledger (curation_ledger.json): every reading is
flushed the moment it's produced, so a credit-out / Gemini daily-cap / crash
loses nothing and a re-run resumes. Each run stops at the per-run Gemini cap;
just run it again to continue.

    python -m ingester.curate --limit 5     # validation
    python -m ingester.curate               # process a run (to the Gemini cap), resumable
    python -m ingester.curate --report      # distribution so far
    python -m ingester.curate --reset
"""
from __future__ import annotations

import json
import re
import sys

from . import classify, config, pdf
from .checkpoint import DONE, FAILED, NEEDS_HUMAN, Ledger, atomic_write_json
from .net import get
from .taxonomy import CATEGORY_NAMES
from .verify import load_candidates

LEDGER_PATH = config.ROOT / "ingester" / "curation_ledger.json"
TEXTS_FILE = config.ROOT / "ingester" / "candidate_texts.json"
HANDOFF_DIR = config.ROOT / "ingester" / "handoff"          # paste-ready prompts (out)
HANDOFF_OUT_DIR = config.ROOT / "ingester" / "handoff_out"  # the AI's JSON replies (in)
CANDIDATES_FILE = config.ROOT / "ingester" / "candidates.json"


def _has_greek(t: str, need: int = 20) -> bool:
    """True if the text really contains Greek (guards against mojibake — Greek
    bytes mis-decoded as Latin-1 contain almost no real Greek letters)."""
    if not t:
        return False
    g = sum(1 for c in t[:1500] if "Ͱ" <= c <= "Ͽ" or "ἀ" <= c <= "῿")
    return g >= need


def _demojibake(t: str) -> str:
    """Recover Greek wrongly decoded as Latin-1 (old ΦΕΚ PDFs / windows-1253 pages):
    re-encode to the original bytes and decode as windows-1253. No-op if already Greek."""
    if not t or _has_greek(t):
        return t
    try:
        fixed = t.encode("latin-1", errors="ignore").decode("windows-1253", errors="ignore")
    except Exception:  # noqa: BLE001
        return t
    return fixed if _has_greek(fixed) else t


def _get_text(rec: dict) -> str:
    """Official ΦΕΚ PDF text, falling back to the e-nomothesia .html (pre-2000)."""
    url = rec.get("official_url")
    text = pdf.extract_text(url) if url else ""
    if text and len(text) > 200:
        return _demojibake(text)[: config.PDF_MAX_CHARS]
    src = rec.get("source_url")
    if not src:
        return ""
    try:
        resp = get(src)
    except Exception:  # noqa: BLE001
        return ""
    # Older e-nomothesia pages are windows-1253/iso-8859-7; requests guesses
    # Latin-1 → mojibake. Try encodings in order, keep the first yielding Greek.
    html = ""
    for enc in (resp.apparent_encoding, "windows-1253", "iso-8859-7", "utf-8", "latin-1"):
        if not enc:
            continue
        try:
            cand = resp.content.decode(enc, errors="strict")
        except (UnicodeDecodeError, LookupError):
            continue
        html = cand
        if _has_greek(cand):
            break
    t = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return _demojibake(t)[: config.PDF_MAX_CHARS]


def prefetch(primary_only: bool = True) -> dict:
    """Download + cache each candidate's text (FREE — no Claude/Gemini). Subagents
    then read from this cache, spending credits only on judgment, not fetching.
    Checkpointed: stored every 10 fetches + resumable (skips cached ids)."""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    recs = load_candidates(primary_only=primary_only)
    store: dict = {}
    if TEXTS_FILE.exists():
        try:
            store = json.loads(TEXTS_FILE.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            store = {}
    print(f"== prefetch · {len(recs)} candidates ({len(store)} already cached) ==")
    done = 0
    for rec in recs:
        rid = rec["id"]
        cached = store.get(rid, {}).get("text")
        if cached and _has_greek(cached):   # re-fetch mojibake/empty entries
            continue
        text = _get_text(rec)
        store[rid] = {
            "title": rec.get("title", ""), "doc_type": rec.get("doc_type"),
            "official_url": rec.get("official_url"), "source_url": rec.get("source_url"),
            "text": text[:3000], "chars": len(text),
        }
        done += 1
        if done % 10 == 0:
            atomic_write_json(TEXTS_FILE, store)
            print(f"  prefetched {done} new (cache {len(store)})")
    atomic_write_json(TEXTS_FILE, store)
    have = sum(1 for v in store.values() if v.get("text"))
    print(f"== prefetch done: cache {len(store)}, {have} with text, "
          f"{len(store) - have} empty (bad/scanned) ==")
    return store


def _ai_read(text: str) -> dict | None:
    cat_list = "\n".join(f"- {c}" for c in CATEGORY_NAMES)
    prompt = (
        "Είσαι ταξινομητής/ελεγκτής ελληνικής εκπαιδευτικής νομοθεσίας. Σου δίνω "
        "το ΠΡΑΓΜΑΤΙΚΟ κείμενο μιας πράξης (ΦΕΚ ή ενοποιημένο). Με βάση ΜΟΝΟ το "
        "κείμενο (όχι τη μνήμη σου), επίστρεψε JSON με πεδία:\n"
        "- subject: μονόγραμμη περιγραφή του τι ρυθμίζει\n"
        "- categories: 1-3 ΑΠΟΚΛΕΙΣΤΙΚΑ από τη λίστα (ή [] αν καμία δεν ταιριάζει)\n"
        "- education_relevant: true αν αφορά πρωτοβάθμια/δευτεροβάθμια εκπαίδευση, αλλιώς false\n"
        "- level: 'Πρωτοβάθμια' | 'Δευτεροβάθμια' | 'Όλες'\n"
        "- in_force_signal: 'in_force' | 'superseded' | 'unclear' (ένδειξη ισχύος από το κείμενο)\n"
        "- note: σύντομη αιτιολογία (π.χ. αν εντοπίζεις «καταργείται/αντικαθίσταται»)\n\n"
        f"ΚΑΤΗΓΟΡΙΕΣ:\n{cat_list}\n\nΚΕΙΜΕΝΟ:\n{text[:8000]}\n\n"
        "Απάντησε ΜΟΝΟ με έγκυρο JSON."
    )
    data = classify.gemini_json(prompt)
    if not isinstance(data, dict):
        return None
    cats = [c for c in data.get("categories", []) if c in CATEGORY_NAMES][:3]
    return {
        "subject": str(data.get("subject", ""))[:200],
        "categories": cats,
        "education_relevant": bool(data.get("education_relevant")),
        "level": data.get("level", "Όλες"),
        "in_force_signal": data.get("in_force_signal", "unclear"),
        "note": str(data.get("note", ""))[:200],
    }


def run(limit: int | None = None, primary_only: bool = True) -> dict:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    recs = load_candidates(primary_only=primary_only)
    by_id = {r["id"]: r for r in recs}
    led = Ledger(LEDGER_PATH)
    led.add_many((r["id"], {"title": r.get("title", ""),
                            "official_url": r.get("official_url"),
                            "source_url": r.get("source_url")}) for r in recs)
    todo = [i for i in led.pending_ids() if i in by_id]
    if limit is not None:
        todo = todo[:limit]
    cap = config.GEMINI_MAX_CALLS_PER_RUN
    print(f"== AI curate · {len(recs)} candidates · {len(todo)} to read this run "
          f"(settled: {len(led.items) - len(led.pending_ids())}) · Gemini cap {cap} ==")

    stopped = False
    misses = 0
    for rid in todo:
        if classify.gemini_call_count() >= cap - 1:
            print("-> Gemini per-run cap reached — re-run to continue (resumable).")
            stopped = True
            break
        rec = by_id[rid]
        led.bump_attempt(rid)
        text = _get_text(rec)
        if not text:
            led.mark(rid, FAILED, error="no text (PDF+HTML both empty)")
            print(f"  ✗ no-text  {(rec.get('title') or '')[:60]}")
            continue
        reading = _ai_read(text)
        if reading is None:
            misses += 1
            print(f"  · gemini-miss (will retry) {(rec.get('title') or '')[:50]}")
            if misses >= 3:   # 3 in a row → daily quota likely exhausted; stop cleanly
                print("-> 3 συνεχόμενες αστοχίες (πιθανό ημερήσιο όριο Gemini εξαντλήθηκε) "
                      "— σταματώ· τρέξε ξανά αργότερα (resumable).")
                stopped = True
                break
            continue
        misses = 0
        status = NEEDS_HUMAN if reading["in_force_signal"] == "unclear" else DONE
        led.mark(rid, status, data={**led.get(rid)["data"], "reading": reading})
        rel = "EDU" if reading["education_relevant"] else "off-topic"
        sig = {"in_force": "✓", "superseded": "⊘", "unclear": "?"}.get(reading["in_force_signal"], "?")
        cats = ", ".join(reading["categories"]) or "—"
        print(f"  {sig} [{rel:<9}] {cats[:34]:<34} | {reading['subject'][:40]}")

    print(f"\n== ledger: {led.summary()} == {'(capped — resume)' if stopped else ''}")
    print(f"   {LEDGER_PATH}")
    return led.summary()


def _all_candidate_meta() -> dict:
    """{id: candidate} for ALL candidates (any type) from candidates.json."""
    if not CANDIDATES_FILE.exists():
        return {}
    try:
        cands = json.loads(CANDIDATES_FILE.read_text(encoding="utf-8")).get("candidates", [])
    except Exception:  # noqa: BLE001
        return {}
    return {c["id"]: c for c in cands if c.get("id")}


def _merge_files(files: list[str], *, by: str, delete: bool) -> dict:
    """Merge reading-array JSON files into the curation ledger. Accepts NEW candidate
    ids (e.g. secondary acts curated via free-credit hand-off), adding them to the
    ledger on the fly. Shared by --apply and --handoff-apply."""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    led = Ledger(LEDGER_PATH)
    meta = _all_candidate_meta()
    applied = bad = skipped = 0
    for p in files:
        try:
            arr = json.loads(open(p, encoding="utf-8").read())
        except Exception:  # noqa: BLE001
            print(f"  ! unreadable JSON: {p}"); bad += 1; continue
        for r in arr if isinstance(arr, list) else []:
            rid = r.get("id")
            if not rid or rid not in meta:
                skipped += 1
                continue
            reading = {
                "subject": str(r.get("subject", ""))[:200],
                "categories": [c for c in r.get("categories", []) if c in CATEGORY_NAMES][:3],
                "education_relevant": bool(r.get("education_relevant")),
                "level": r.get("level", "Όλες"),
                "in_force_signal": r.get("in_force_signal", "unclear"),
                "note": str(r.get("note", ""))[:200],
                "by": by,
            }
            if rid not in led.items:
                c = meta[rid]
                led.add(rid, {"title": c.get("title", ""),
                              "official_url": c.get("official_url"),
                              "source_url": c.get("source_url")})
            status = NEEDS_HUMAN if reading["in_force_signal"] == "unclear" else DONE
            led.mark(rid, status, data={**led.get(rid)["data"], "reading": reading})
            applied += 1
        if delete:
            try:
                __import__("os").unlink(p)
            except OSError:
                pass
    extra = (f" ({bad} unreadable)" if bad else "") + (f" ({skipped} unknown-id skipped)" if skipped else "")
    print(f"applied {applied} readings from {len(files)} file(s){extra}; ledger {led.summary()}")
    return led.summary()


def apply_readings_glob() -> dict:
    """Merge subagent outputs (ingester/_out_*.json) into the ledger, then delete
    them — the per-dose checkpoint after a batch of subagents finishes."""
    import glob
    files = sorted(glob.glob(str(config.ROOT / "ingester" / "_out_*.json")))
    return _merge_files(files, by="claude-subagent", delete=True)


def handoff_apply() -> dict:
    """Merge the JSON replies brought back from free-credit Opus
    (ingester/handoff_out/*.json) into the ledger. Files are KEPT (not deleted)."""
    import glob
    # Accept replies saved in EITHER handoff_out/ or handoff/ (only .json — the
    # .txt prompts are ignored), so it works wherever the user dropped them.
    files = sorted(set(glob.glob(str(HANDOFF_OUT_DIR / "*.json"))
                       + glob.glob(str(HANDOFF_DIR / "*.json"))))
    if not files:
        print(f"no .json replies in {HANDOFF_OUT_DIR} or {HANDOFF_DIR}")
        return {}
    return _merge_files(files, by="handoff-opus", delete=False)


def make_handoff(per_batch: int = 50, max_chars: int = 1400, primary_only: bool = False) -> int:
    """Write paste-ready prompt files (instructions + embedded law texts) for the
    UNCURATED candidates, so the bulk reading can run on free-credit Opus elsewhere.
    User pastes each handoff/batch_XXX.txt into the AI, saves the JSON reply to
    handoff_out/batch_XXX.json, then runs --handoff-apply."""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    recs = load_candidates(primary_only=primary_only)
    texts = json.loads(TEXTS_FILE.read_text(encoding="utf-8")) if TEXTS_FILE.exists() else {}
    led = Ledger(LEDGER_PATH)
    curated = {i for i, it in led.items.items() if it.get("data", {}).get("reading")}
    pending = [r for r in recs if r["id"] not in curated
               and texts.get(r["id"], {}).get("text")]
    header = (
        "Είσαι ταξινομητής ελληνικής εκπαιδευτικής νομοθεσίας (Πρωτοβάθμια & "
        "Δευτεροβάθμια). Κρίνε ΜΟΝΟ από το κείμενο, όχι από μνήμη.\n"
        "Για ΚΑΘΕ αντικείμενο στον πίνακα JSON «ΔΕΔΟΜΕΝΑ», βγάλε ένα reading:\n"
        '- "id": αντέγραψέ το ΑΥΤΟΥΣΙΟ\n'
        '- "subject": μονόγραμμη περιγραφή (≤120 χαρ.)\n'
        '- "categories": 1-3 ΑΥΤΟΥΣΙΑ από αυτή τη λίστα (ή [] αν καμία): '
        + json.dumps(CATEGORY_NAMES, ensure_ascii=False) + "\n"
        '- "education_relevant": true ΜΟΝΟ αν αφορά πρωτοβάθμια/δευτεροβάθμια '
        "εκπαίδευση (false για τριτοβάθμια, στρατιωτικές σχολές, γενική διοίκηση, "
        "διεθνείς συμβάσεις, αθλητικά)\n"
        '- "level": "Πρωτοβάθμια" | "Δευτεροβάθμια" | "Όλες"\n'
        '- "in_force_signal": "in_force" | "superseded" | "unclear"\n'
        '- "note": ≤120 χαρ. αιτιολογία (ιδίως «καταργείται/αντικαθίσταται»)\n'
        "ΕΞΟΔΟΣ: ΜΟΝΟ ένα έγκυρο JSON array των readings, ίδια σειρά, χωρίς άλλο κείμενο.\n\n"
        "ΔΕΔΟΜΕΝΑ:\n"
    )
    HANDOFF_DIR.mkdir(parents=True, exist_ok=True)
    HANDOFF_OUT_DIR.mkdir(parents=True, exist_ok=True)
    n = 0
    for i in range(0, len(pending), per_batch):
        chunk = pending[i:i + per_batch]
        data = [{"id": r["id"], "title": r.get("title", ""),
                 "text": texts[r["id"]]["text"][:max_chars]} for r in chunk]
        (HANDOFF_DIR / f"batch_{n:03d}.txt").write_text(
            header + json.dumps(data, ensure_ascii=False), encoding="utf-8")
        n += 1
    print(f"== wrote {n} hand-off files to {HANDOFF_DIR} "
          f"({len(pending)} uncurated candidates, {per_batch}/file) ==")
    print(f"-> paste each batch_XXX.txt into free Opus; save its JSON reply to")
    print(f"   {HANDOFF_OUT_DIR}\\batch_XXX.json  then:  python -m ingester.curate --handoff-apply")
    return n


def _report() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    if not LEDGER_PATH.exists():
        print("no curation yet — run the AI pass first.")
        return 1
    d = json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
    from collections import Counter
    edu = Counter(); sig = Counter(); cats = Counter()
    for it in d.get("items", []):
        r = it.get("data", {}).get("reading")
        if not r:
            continue
        edu["education_relevant" if r["education_relevant"] else "off-topic"] += 1
        sig[r["in_force_signal"]] += 1
        for c in r["categories"]:
            cats[c] += 1
    print("education relevance:", dict(edu))
    print("in-force signal:    ", dict(sig))
    print("topic coverage (AI-classified):")
    for c, n in cats.most_common():
        print(f"   {n:>3}  {c}")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if "--reset" in argv:
        if LEDGER_PATH.exists():
            LEDGER_PATH.unlink()
        print("curation ledger reset.")
        return 0
    if "--prefetch" in argv:
        prefetch(primary_only="--all-types" not in argv)
        return 0
    if "--apply" in argv:
        apply_readings_glob()
        return 0
    if "--handoff" in argv:
        pb = 50
        for i, a in enumerate(argv):
            if a == "--per-batch" and i + 1 < len(argv):
                pb = int(argv[i + 1])
        make_handoff(per_batch=pb, primary_only="--primary-only" in argv)
        return 0
    if "--handoff-apply" in argv:
        handoff_apply()
        return 0
    if "--report" in argv:
        return _report()
    limit = None
    for i, a in enumerate(argv):
        if a == "--limit" and i + 1 < len(argv):
            limit = int(argv[i + 1])
    run(limit=limit, primary_only="--all-types" not in argv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
