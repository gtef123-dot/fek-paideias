"""Lightweight self-tests — no network, no AI. Run: python -m ingester.selftest

Guards against silent regressions in classification, normalization and the
seed/knowledge corpora. Exits non-zero on failure (used in CI before ingest).
"""
from __future__ import annotations

import json
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

from . import config, knowledge, seed
from .taxonomy import classify_categories, classify_levels, normalize

# (title, expected category that MUST appear)
CASES = [
    ("Εγκύκλιος μεταθέσεων εκπαιδευτικών Δ.Ε. 2026-2027", "Μεταθέσεις"),
    ("Χορήγηση κανονικής άδειας εκπαιδευτικού", "Άδειες (κανονική / αναρρωτική / γονική κ.λπ.)"),
    ("Άδεια άσκησης ιδιωτικού έργου με αμοιβή", "Άδεια Άσκησης Ιδιωτικού Έργου / Επαγγέλματος"),
    ("Καθορισμός Τομέων και Ειδικοτήτων ανά ΕΠΑ.Λ.", "ΕΠΑΛ / Επαγγελματική Εκπαίδευση"),
    ("Πρόγραμμα Πανελλαδικών Εξετάσεων 2026", "Εξετάσεις & Εισαγωγή στην Τριτοβάθμια"),
    ("Παράλληλη στήριξη μαθητή με γνωμάτευση ΚΕΔΑΣΥ", "Ειδική Αγωγή & Εκπαίδευση (ΕΑΕ)"),
]

SYNONYMS = [
    (["γεννησ", "τοκετ", "εγκυ", "εγκυμον", "μωρο", "γεννηθ", "γεννησα"],
     ["κυησ", "μητροτητ", "λοχει", "ανατροφ", "πατροτητ"]),
    (["αρρωστ", "ασθεν", "αναρρωσ", "αδιαθ", "γριπ"], ["αναρρωτικ"]),
    (["κηδει", "πενθ", "χασα", "θανατ", "απεβιωσ", "πεθαν"], ["πενθ"]),
    (["λεφτα", "πληρωμ", "πληρωθ", "χρηματ", "αμοιβ", "μισθο"],
     ["μισθολογ", "αποδοχ", "αποζημιωσ"]),
    (["μετακινηθ", "αλλαγη σχολει", "φυγω απο", "μεταφερθ"],
     ["μεταθεσ", "αποσπασ", "τοποθετησ"]),
    (["μεσα στην ταξη", "συνεκπαιδ", "μαζι με τον δασκαλο", "δευτερος δασκαλ"],
     ["συνδιδασκ", "παραλληλ στηριξ", "ειδικ αγωγ"]),
    (["δευτερο σχολει", "δυο σχολει", "αλλο σχολει", "συμπληρων"],
     ["συμπληρωσ", "διαθεσ", "ωραριο"]),
    (["διοριστηκ", "μονιμοποιηθ", "εγινα μονιμ", "νεοδιοριστ"],
     ["διορισμ", "μονιμοποιησ"]),
    (["προσληφθ", "αναπληρωτ", "οπσυδ"], ["προσληψ", "αναπληρωτ"]),
    (["πανελλ", "βασ", "μηχανογραφ", "υποψηφι"], ["εξετασ", "πανελλαδικ", "εισακτε"]),
    (["απουσι", "λειψω", "αδει"], ["αδει"]),
]

STOP_TERMS = {
    "να", "για", "τον", "την", "το", "του", "τη", "της", "των", "τα", "οι", "ο", "η",
    "μου", "σου", "μασ", "σασ", "και", "σε", "στο", "στη", "στην", "στουσ", "στισ",
    "με", "απο", "ως", "αν", "τι", "πωσ", "ποσο", "ποια", "ποιο", "ποιοσ", "ποιαν",
    "ειμαι", "εχω", "θελω", "μπορω", "παιρνω", "δικαιουμαι", "ισχυει", "μηπωσ",
}

RETRIEVAL_CASES = [
    # The precise answer to a marriage-leave question is the marriage-leave LAW
    # itself — ν.4808/2021 αρ.39 (= seed:101), which ranks #1. The old assertion
    # used a knowledge card as a proxy, but that card never mentions γάμος and
    # slipped below specific Διαύγεια άδεια acts once the corpus grew ~15×, so we
    # assert the genuinely-correct top hit instead.
    ("μπορώ να λείψω για τον γάμο μου", "seed:101/Α/2021", 3),
    ("αρρώστησα τι άδεια παίρνω αναπληρωτής", "knowledge:adeies-anaplirotes-espa", 5),
    ("συνδιδασκαλία παράλληλη στήριξη μέσα στην τάξη", "knowledge:eae-syndidaskalia", 3),
]


def _client_terms(query: str) -> list[str]:
    norm_q = normalize(query)
    terms = [
        t for t in norm_q.split()
        if t and t not in STOP_TERMS and (len(t) >= 3 or any(ch.isdigit() for ch in t))
    ]
    if len(terms) > 1:
        terms = [t for t in terms if not (len(t) == 4 and t[:2] in {"19", "20"} and t.isdigit())]
    extra = dict.fromkeys(terms)
    for keys, additions in SYNONYMS:
        if any(k in norm_q for k in keys):
            for a in additions:
                if a not in STOP_TERMS and (len(a) >= 3 or any(ch.isdigit() for ch in a)):
                    extra[a] = None
    return list(extra)


def _client_score(record: dict, terms: list[str]) -> int:
    title = normalize(record.get("title", ""))
    summary = normalize(" ".join([record.get("summary") or "", record.get("summary_ai") or ""]))
    detail = normalize(" ".join([
        " ".join(record.get("keywords") or []),
        " ".join(record.get("articles") or []),
        " ".join(record.get("excerpts") or []),
    ]))
    tags = normalize(" ".join([
        " ".join(record.get("categories") or []),
        " ".join(record.get("levels") or []),
        record.get("doc_type") or "",
    ]))
    ids = normalize(" ".join([record.get("fek_label") or "", record.get("ada") or ""]))

    score = hits = 0
    for term in terms:
        term_score = 0
        if term in title:
            term_score += 4
        if term in tags:
            term_score += 3
        if term in detail:
            term_score += 3
        if term in summary:
            term_score += 2
        if term in ids:
            term_score += 1
        if term_score:
            hits += 1
            score += term_score
    if not hits:
        return 0
    auth = {"primary_law": 6, "official_circular": 5, "diavgeia_decision": 4,
            "official_guide": 3, "secondary_guide": 2, "unknown": 1}
    score += auth.get(record.get("authority_level"), 1) * 1.5
    if record.get("source") == "knowledge":
        score += 4
    if record.get("enriched"):
        score += 2
    return score


def _search_records(query: str, records: list[dict]) -> list[dict]:
    terms = _client_terms(query)
    scored = [(r, _client_score(r, terms)) for r in records]
    scored = [(r, s) for r, s in scored if s > 0]
    scored.sort(key=lambda x: (x[1], x[0].get("date") or ""), reverse=True)
    return [r for r, _ in scored]


def main() -> int:
    fails = 0

    # Normalization (accents, final sigma, acronym dots)
    assert normalize("ΕΠΑ.Λ.") == "επαλ", normalize("ΕΠΑ.Λ.")
    assert normalize("Μεταθέσεις") == "μεταθεσεισ", normalize("Μεταθέσεις")

    # Classification
    for title, expected in CASES:
        cats = classify_categories(title)
        ok = expected in cats
        print(f"  {'OK  ' if ok else 'FAIL'} {title[:46]:46} → {cats}")
        if not ok:
            fails += 1

    # Levels
    assert "Δευτεροβάθμια" in classify_levels(normalize("Γενικό Λύκειο"))
    assert "Πρωτοβάθμια" in classify_levels(normalize("Νηπιαγωγείο"))

    # Event detection (amendments/repeals) — precision guards
    from . import events
    assert events.change_targets("Τροποποίηση του άρθρου 3 του ν. 4823/2021") == {("4823", "2021")}
    assert events.change_targets("Αναφορά στον ν. 4823/2021 χωρίς μεταβολή") == set()
    assert events.primary_law_key("Νόμος 3699/2008 — όπως τροπ. με ν. 4823/2021") == ("3699", "2008")

    # Corpora load
    seeds, cards = seed.records(), knowledge.records()
    assert len(seeds) >= 15, f"seed corpus too small: {len(seeds)}"
    assert len(cards) >= 1, "no knowledge cards"
    # Knowledge cards must carry concrete content (articles)
    assert all(c.get("articles") for c in cards), "a knowledge card has no articles"

    # Frontend-like retrieval golden tests against the published JSON.
    assert config.JSON_OUT.exists(), f"missing {config.JSON_OUT}"
    payload = json.loads(config.JSON_OUT.read_text(encoding="utf-8"))
    records = payload.get("records", [])
    for query, expected_id, top_n in RETRIEVAL_CASES:
        top = _search_records(query, records)[:top_n]
        top_ids = [r.get("id") for r in top]
        ok = expected_id in top_ids
        print(f"  {'OK  ' if ok else 'FAIL'} retrieval {query[:34]:34} → {top_ids}")
        if not ok:
            fails += 1

    print(f"== seeds={len(seeds)} knowledge={len(cards)} | total fails={fails} ==")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
