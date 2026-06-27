"""Grounded verifier: confirm a cornerstone law against its ACTUAL official text.

The point made to the user: an LLM "from memory" hallucinates law numbers/dates —
useless. An LLM (or plain code) checking against the FETCHED ΦΕΚ text is reliable.
So this verifies every cornerstone the same disciplined way:

  Deterministic core (free, no credits):
    1. fetch the official ΦΕΚ PDF text (pdf.extract_text) — proves it resolves.
    2. number_present: the law's own number actually appears in that text.
    3. title_terms:   the title's key terms appear in the text (right document).
    4. self-repeal:   scan the text for a repeal marker on this very law.
    5. amendments:    surface any incoming acts already flagged on it (affected_by).

  Optional AI judgment (Gemini, only for ambiguous items):
    given the text + our claim, does the text SUPPORT it / show it superseded?

The outcome is a CONFIDENCE SIGNAL with evidence — never a bare "in force: true".
Everything is checkpointed through checkpoint.Ledger, so a credit-out / crash
loses nothing: each verdict is flushed to disk the moment it's produced, and a
re-run skips what's already settled.

    python -m ingester.verify --limit 6          # deterministic, ~free
    python -m ingester.verify --limit 6 --ai      # + Gemini semantic check
    python -m ingester.verify --report            # ledger summary
    python -m ingester.verify --reset             # start the ledger over
"""
from __future__ import annotations

import json
import re
import sys

from . import classify, config, events, pdf
from .checkpoint import DONE, FAILED, NEEDS_HUMAN, Ledger
from .taxonomy import normalize

LEDGER_PATH = config.ROOT / "ingester" / "verification_ledger.json"
CAND_LEDGER_PATH = config.ROOT / "ingester" / "candidate_verification_ledger.json"
CANDIDATES_FILE = config.ROOT / "ingester" / "candidates.json"
CORNERSTONE_LEVELS = {"primary_law", "official_circular"}
PRIMARY_DOC_TYPES = {"Νόμος", "Προεδρικό Διάταγμα", "ΠΝΠ"}

# Words too generic to prove we fetched the RIGHT document.
_STOP = {normalize(w) for w in (
    "νομος", "υα", "πδ", "αρθρο", "αρθρα", "και", "του", "της", "των", "για",
    "εκπαιδευση", "εκπαιδευτικων", "σχολικων", "μοναδων", "διαταγμα", "αποφαση",
    "υπουργικη", "προεδρικο", "οπως", "ισχυει", "φεκ",
)}
_ARTICLE_REF = re.compile(r"αρθρ[ωοα]\s*(\d{1,3})")


def load_cornerstones(require_pdf: bool = False) -> list[dict]:
    """Cornerstone records (in-force, authoritative) from the sharded index."""
    if not config.INDEX_OUT.exists():
        raise SystemExit(f"!! Δεν βρέθηκε {config.INDEX_OUT} — τρέξε πρώτα τον ingester.")
    recs = json.loads(config.INDEX_OUT.read_text(encoding="utf-8")).get("records", [])
    out = []
    for r in recs:
        if (r.get("authority_level") in CORNERSTONE_LEVELS
                and (r.get("status") or "") != "ΚΑΤΑΡΓΗΘΕΝ"):
            url = r.get("official_url") or ""
            if require_pdf and "blob.core.windows.net" not in url and "/fek/" not in url:
                continue
            out.append(r)
    return out


def load_candidates(primary_only: bool = True) -> list[dict]:
    """Candidate laws harvested by harvest.py (the bulk pool), with a fetchable URL."""
    if not CANDIDATES_FILE.exists():
        raise SystemExit("!! Δεν βρέθηκε candidates.json — τρέξε python -m ingester.harvest")
    cands = json.loads(CANDIDATES_FILE.read_text(encoding="utf-8")).get("candidates", [])
    out = []
    for c in cands:
        if primary_only and c.get("doc_type") not in PRIMARY_DOC_TYPES:
            continue
        if not c.get("official_url"):
            continue
        out.append(c)
    return out


def _title_terms(title: str) -> list[str]:
    toks = [t for t in normalize(title).split() if len(t) > 4 and t not in _STOP
            and not t.isdigit()]
    # de-dup, keep order, cap — enough to fingerprint the document
    seen, terms = set(), []
    for t in toks:
        if t not in seen:
            seen.add(t)
            terms.append(t)
    return terms[:8]


def verify_record(rec: dict, text: str, use_ai: bool = False) -> dict:
    """Produce a grounded verdict for one cornerstone given its fetched text."""
    title = rec.get("title", "")
    evidence: list[dict] = []
    fetched = bool(text and len(text) > 200)
    evidence.append({"check": "fetch_official_text", "ok": fetched,
                     "detail": f"{len(text)} chars" if text else "no text/extract failed"})

    if not fetched:
        return {"status": "unverified", "confidence": 0.0, "fetched": False,
                "evidence": evidence,
                "note": "Δεν ανακτήθηκε επίσημο κείμενο (κακό/σαρωμένο PDF ή μη-PDF URL)."}

    ntext = normalize(text)

    # 2. number_present — the law's OWN identifying number(s) appear in the text.
    fek_label = rec.get("fek_label") or (rec.get("fek") or {}).get("label")
    id_numbers = {str(fek_label).split("/")[0].split()[-1]} if fek_label else set()
    id_numbers |= {n for n, _y in events.law_keys(title)}
    id_numbers = {n for n in id_numbers if n and n.isdigit()}
    nums_found = sorted(n for n in id_numbers if n in ntext)
    number_present = bool(nums_found)
    evidence.append({"check": "law_number_in_text", "ok": number_present,
                     "detail": f"found {nums_found}" if nums_found else f"none of {sorted(id_numbers)}"})

    # 3. title_terms — did we fetch the RIGHT document? Tri-state: True / False /
    # None (N/A — bare ID-only titles like "Υ.Α. 75707/Δ2/2026 - ΦΕΚ 3358/Β/..."
    # carry no descriptive terms; for those we rely on the number grounding).
    terms = _title_terms(title)
    hits = [t for t in terms if t in ntext]
    frac = (len(hits) / len(terms)) if terms else 0.0
    title_ok = (frac >= 0.4) if terms else None
    evidence.append({"check": "title_terms_in_text", "ok": title_ok,
                     "detail": (f"{len(hits)}/{len(terms)} key terms ({frac:.0%})"
                                if terms else "N/A (τίτλος μόνο-ID)")})

    # 4. currency — a law never repeals ITSELF in its own ΦΕΚ; repeals come from
    # LATER acts, which event-detection already records in `affected_by`. So we
    # rely on that (below) for currency, not on scanning the law's own text
    # (which legitimately contains «καταργείται» clauses about OTHER provisions
    # and produced false positives on in-force laws like ν.4808/2021).

    # 5. known amendments already flagged by event-detection.
    amendments = rec.get("affected_by") or []
    if amendments:
        evidence.append({"check": "known_amendments", "ok": False,
                         "detail": f"{len(amendments)} incoming act(s) flag this law"})

    # ── verdict from deterministic signals ──
    # strong_id = ≥2 independent identifiers (act # + ΦΕΚ #) found together → this
    # is definitely the right official document, even without descriptive terms.
    strong_id = len(nums_found) >= 2
    if title_ok is None:                       # bare ID-only title → lean on numbers
        if strong_id:
            status, confidence = "verified", 0.85
        elif number_present:
            status, confidence = "partially_verified", 0.6
        else:
            status, confidence = "needs_human", 0.3
    elif number_present and title_ok:
        status, confidence = "verified", 0.9
    elif number_present or title_ok:
        status, confidence = "partially_verified", 0.65
    else:
        status, confidence = "needs_human", 0.3
    if amendments and status == "verified":
        status, confidence = "partially_verified", 0.7  # in force but amended — caveat

    # ── optional AI semantic judgment (only when asked) ──
    if use_ai:
        ai = _ai_judgment(title, text)
        if ai is not None:
            evidence.append({"check": "ai_supports_claim", "ok": ai.get("supports"),
                             "detail": ai.get("note", "")[:200]})
            if ai.get("superseded_signal") or ai.get("supports") is False:
                status, confidence = "needs_human", min(confidence, 0.35)
            elif status == "partially_verified" and ai.get("supports"):
                confidence = min(0.85, confidence + 0.15)

    return {"status": status, "confidence": round(confidence, 2), "fetched": True,
            "number_present": number_present, "title_match": round(frac, 2),
            "amendments": len(amendments), "evidence": evidence}


def _ai_judgment(title: str, text: str) -> dict | None:
    """Grounded AI check: does the FETCHED text support our claim? Superseded?"""
    prompt = (
        "Είσαι νομικός ελεγκτής. Σου δίνω τον ΤΙΤΛΟ μιας εγγραφής εκπαιδευτικής "
        "νομοθεσίας και ΑΠΟΣΠΑΣΜΑ από το ΠΡΑΓΜΑΤΙΚΟ επίσημο κείμενο (ΦΕΚ). "
        "Με βάση ΜΟΝΟ το απόσπασμα (όχι τη μνήμη σου), κρίνε: (1) στηρίζει το "
        "κείμενο ότι αυτή είναι η σωστή πράξη; (2) υπάρχει ένδειξη ότι έχει "
        "καταργηθεί/αντικατασταθεί;\n\n"
        f"ΤΙΤΛΟΣ: {title}\n\nΑΠΟΣΠΑΣΜΑ:\n{text[:6000]}\n\n"
        'Απάντησε ΜΟΝΟ JSON: {"supports": true/false, "superseded_signal": '
        'true/false, "note": "σύντομη αιτιολογία"}.'
    )
    data = classify.gemini_json(prompt)
    return data if isinstance(data, dict) else None


def run(limit: int, use_ai: bool = False, require_pdf: bool = True,
        candidates: bool = False) -> dict:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    if candidates:
        recs = load_candidates(primary_only=True)
        led = Ledger(CAND_LEDGER_PATH)
        kind = "candidate primary-laws (Νόμος/ΠΔ)"
    else:
        recs = load_cornerstones(require_pdf=require_pdf)
        led = Ledger(LEDGER_PATH)
        kind = "cornerstones"
    by_id = {r["id"]: r for r in recs}
    added = led.add_many((r["id"], {"title": r.get("title", ""),
                                    "url": r.get("official_url")}) for r in recs)
    print(f"== grounded verify · {len(recs)} {kind} "
          f"({added} new in ledger) · ai={'on' if use_ai else 'off'} ==")

    todo = [i for i in led.pending_ids() if i in by_id][:limit]
    print(f"-> {len(todo)} to process this batch (limit {limit}); "
          f"already settled: {len(led.items) - len(led.pending_ids())}")

    for rid in todo:
        rec = by_id[rid]
        led.bump_attempt(rid)                       # counted BEFORE the risky fetch
        text = pdf.extract_text(rec.get("official_url") or "")
        verdict = verify_record(rec, text, use_ai=use_ai)
        v = verdict["status"]
        led_status = {"verified": DONE, "partially_verified": DONE,
                      "needs_human": NEEDS_HUMAN, "unverified": FAILED}[v]
        led.mark(rid, led_status, data={**led.get(rid)["data"], "verdict": verdict},
                 error=verdict.get("note"))
        tick = {"verified": "✓", "partially_verified": "≈",
                "needs_human": "⚠", "unverified": "✗"}[v]
        print(f"  {tick} [{v:<18} {verdict['confidence']:.2f}] {(rec.get('title') or '')[:64]}")

    print(f"\n== ledger: {led.summary()} ==")
    print(f"   {led.path}")
    return led.summary()


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    cand = "--candidates" in argv
    ledger_path = CAND_LEDGER_PATH if cand else LEDGER_PATH
    if "--reset" in argv:
        if ledger_path.exists():
            ledger_path.unlink()
        print("ledger reset.")
        return 0
    if "--report" in argv:
        led = Ledger(ledger_path)
        print(json.dumps(led.summary(), ensure_ascii=False, indent=1))
        return 0
    limit = 6
    for i, a in enumerate(argv):
        if a == "--limit" and i + 1 < len(argv):
            limit = int(argv[i + 1])
    run(limit=limit, use_ai="--ai" in argv, require_pdf="--all" not in argv,
        candidates=cand)
    return 0


if __name__ == "__main__":
    sys.exit(main())
