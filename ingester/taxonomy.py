"""The education-legislation taxonomy + Greek text normalization.

Single source of truth for categories. The frontend derives its category list
from the data itself, so we never duplicate this list in JavaScript.

Each keyword is either a STEM (str) or a GROUP OF STEMS (tuple) that must ALL be
present. Stems match as substrings against accent-stripped, dot-joined,
final-sigma-unified text, so one stem covers every inflection
(μετάθεση/μεταθέσεις/μεταθέσεων -> "μεταθεσ"). Groups solve multi-word phrases
regardless of inflection/order: ("αδει","ασκησ") matches "Άδεια άσκησης
ιδιωτικού έργου" but not a plain "κανονική άδεια".
"""
from __future__ import annotations

import re
import unicodedata

# ── Greek-aware normalization ────────────────────────────────────────────────
_COMBINING = re.compile(r"[̀-ͯ]")


def normalize(text: str) -> str:
    """Lowercase, strip accents, unify final sigma, join acronym dots, despace.

    Dots are removed (so 'ΕΠΑ.Λ.' -> 'επαλ'); other punctuation becomes a space
    (so words on either side of a comma/parenthesis stay separated).
    """
    if not text:
        return ""
    text = unicodedata.normalize("NFD", text)
    text = _COMBINING.sub("", text)            # drop accents
    text = unicodedata.normalize("NFC", text)
    text = text.lower().replace("ς", "σ")      # unify final sigma
    text = text.replace(".", "")               # join acronym dots: επα.λ -> επαλ
    text = re.sub(r"[^0-9a-zα-ω\s]", " ", text)  # other punctuation -> space
    text = re.sub(r"\s+", " ", text)
    return text.strip()


# ── Topical categories (validated against esos / alfavita / edu.klimaka) ──────
CATEGORIES: list[tuple[str, list]] = [
    ("Διορισμοί Μονίμων (ΑΣΕΠ)", [
        "διορισμ", "διοριστε", "νεοδιοριστ", "μονιμοποιησ",
        ("μονιμ", "εκπαιδευτικ"), "ασεπ", "προκηρυξ", "1γε", "2γε",
        "1εα", "2εα", "3εα",
    ]),
    ("Προσλήψεις Αναπληρωτών", [
        "αναπληρωτ", "ωρομισθι", "προσληψ", "οπσυδ",
        ("προσωριν", "πινακ"), ("αξιολογικ", "πινακ"),
    ]),
    ("Μεταθέσεις", [
        "μεταθεσ", ("αμοιβαι", "μεταθεσ"), ("μοναδ", "μεταθεσ"),
        ("βελτιωσ", "θεσ"),
    ]),
    ("Αποσπάσεις", [
        "αποσπασ", "αποσπασμεν",
    ]),
    ("Τοποθετήσεις & Οργανικές Θέσεις", [
        "τοποθετησ", ("οργανικ", "θεσ"), ("οργανικ", "κεν"), "υπεραριθμ",
        "πλεονασμ", ("λειτουργικ", "κεν"),
    ]),
    ("Άδειες (κανονική / αναρρωτική / γονική κ.λπ.)", [
        ("κανονικ", "αδει"), "αναρρωτικ", ("γονικ", "αδει"), "ανατροφ",
        ("εκπαιδευτικ", "αδει"), ("υπηρεσιακ", "αδει"), ("ανευ", "αποδοχ"),
        "μητροτητ", "λοχει", ("αδει", "κυησ"), ("αδει", "ασθεν"),
        ("ειδικ", "αδει"), ("αδει", "τεκν"), "αδειεσ",
    ]),
    ("Άδεια Άσκησης Ιδιωτικού Έργου / Επαγγέλματος", [
        ("αδει", "ασκησ"), ("ιδιωτικ", "εργ"), ("ασκησ", "επαγγελμ"),
        ("ιδιωτικ", "αμειβ"),
    ]),
    ("Μισθολογικά / Οικονομικά", [
        "μισθολογ", "μισθοδοσ", "αποδοχ", "επιδομ", "κλιμακι", "αναδρομ",
        "οδοιπορικ", "υπερωρι", "εισφορ", "αποζημιωσ",
    ]),
    ("Ωρολόγια & Προγράμματα Σπουδών", [
        "ωρολογι", ("προγραμμα", "σπουδ"), ("αναλυτικ", "προγραμμα"),
        ("διδακτεα", "υλ"), ("αναθεσ", "μαθημ"), ("ωραριο", "διδασκ"),
        "διδασκαλ", ("διδακτικ", "βιβλι"), ("διδακτικ", "πακετ"),
    ]),
    ("Εξετάσεις & Εισαγωγή στην Τριτοβάθμια", [
        "εξετασ", "πανελλαδικ", "πανελληνι", "εισακτε", "μηχανογραφ",
        ("τραπεζα", "θεματ"), "προαγωγικ", "απολυτηρι", "βαθμολογ", "ενδοσχολικ",
    ]),
    ("Αξιολόγηση Εκπαιδευτικών & Σχ. Μονάδων", [
        "αξιολογ", ("συλλογικ", "προγραμματισμ"), "αποτιμησ",
    ]),
    ("Στελέχη / Επιλογή Διευθυντών", [
        ("στελεχ", "εκπαιδευσ"), ("διευθυντ", "σχολ"), "υποδιευθυντ",
        ("συμβουλ", "εκπαιδευσ"), ("διευθυντ", "εκπαιδευσ"), "μοριοδοτησ",
        ("επιλογ", "στελεχ"),
    ]),
    ("Ειδική Αγωγή & Εκπαίδευση (ΕΑΕ)", [
        ("ειδικ", "αγωγ"), "εαε", ("παραλληλ", "στηριξ"), "κεδασυ",
        ("τμημα", "ενταξ"), "σμεαε", "εξατομικευμ", ("ειδικ", "εκπαιδευσ"),
        ("ειδικου", "εκπαιδευτικ"), "εβπ",
    ]),
    ("Σχολική Ζωή & Φοίτηση Μαθητών", [
        "εγγραφ", "μετεγγραφ", "φοιτησ", "απουσι", ("εσωτερικ", "κανονισμ"),
        ("παιδαγωγικ", "ελεγχ"), ("ποιν", "μαθητ"),
    ]),
    ("Πειθαρχικό Εκπαιδευτικών", [
        "πειθαρχ", "παραπτωμ", "αργια", ("ενορκ", "διοικητικ", "εξετασ"),
    ]),
    ("Οργάνωση & Διοίκηση Σχολείων", [
        "ιδρυσ", "συγχωνευσ", "καταργησ", "υποβιβασμ", ("λειτουργ", "σχολ"),
        ("συλλογ", "διδασκοντ"), ("σχολικ", "επιτροπ"),
    ]),
    ("Επιμόρφωση", [
        "επιμορφ", "πεκεσ", "σεμιναρι", "πιστοποιησ",
    ]),
    ("Νηπιαγωγείο / Ολοήμερο", [
        "νηπιαγωγ", "προνηπ", "ολοημερ", "προσχολικ",
    ]),
    ("ΕΠΑΛ / Επαγγελματική Εκπαίδευση", [
        "επαλ", "επασ", "ειδικοτητ", "μαθητει", "μεταλυκειακ",
        ("εργαστηριακ", "κεντρ"), "σαεκ", ("επαγγελματικ", "λυκει"),
        ("επαγγελματικ", "εκπαιδευσ"),
    ]),
    ("Ιδιωτική Εκπαίδευση", [
        ("ιδιωτικ", "σχολ"), ("ιδιωτικ", "εκπαιδευτηρι"), "φροντιστηρι",
        ("κεντρ", "ξενων", "γλωσσ"), "κολλεγι", "κολεγι",
    ]),
]

CATEGORY_NAMES = [name for name, _ in CATEGORIES]
FALLBACK_CATEGORY = "Γενικά Εκπαιδευτικά"


def _norm_group(group) -> tuple[str, ...]:
    """A keyword is either a stem (str) or an all-must-match group (tuple)."""
    tokens = group if isinstance(group, (list, tuple)) else (group,)
    return tuple(normalize(t) for t in tokens)


# Pre-normalize keyword groups once.
_NORM_CATS = [(name, [_norm_group(g) for g in kws]) for name, kws in CATEGORIES]

# ── Level (βαθμίδα) detection ────────────────────────────────────────────────
LEVEL_PRIMARY = [normalize(k) for k in
                 ["πρωτοβαθμ", "δημοτικ", "νηπιαγωγ", "προνηπ", "προσχολικ",
                  "πε70", "πε60"]]
LEVEL_SECONDARY = [normalize(k) for k in
                   ["δευτεροβαθμ", "γυμνασι", "λυκει", "επαλ", "επασ"]]


def classify_levels(norm_text: str) -> list[str]:
    primary = any(k in norm_text for k in LEVEL_PRIMARY)
    secondary = any(k in norm_text for k in LEVEL_SECONDARY)
    if primary and not secondary:
        return ["Πρωτοβάθμια"]
    if secondary and not primary:
        return ["Δευτεροβάθμια"]
    if primary and secondary:
        return ["Πρωτοβάθμια", "Δευτεροβάθμια"]
    return ["Όλες / Γενικό"]


def classify_categories(text: str) -> list[str]:
    """Return up to 3 best-matching categories by keyword-group hit count."""
    norm = normalize(text)
    scored: list[tuple[str, int]] = []
    for name, groups in _NORM_CATS:
        hits = sum(1 for g in groups if all(tok in norm for tok in g))
        if hits:
            scored.append((name, hits))

    # Disambiguate teacher vs student discipline.
    names = {n for n, _ in scored}
    if "Πειθαρχικό Εκπαιδευτικών" in names:
        if "μαθητ" in norm and "εκπαιδευτικ" not in norm:
            scored = [(n, s) for n, s in scored if n != "Πειθαρχικό Εκπαιδευτικών"]
            scored.append(("Σχολική Ζωή & Φοίτηση Μαθητών", 1))

    scored.sort(key=lambda x: x[1], reverse=True)
    return [n for n, _ in scored[:3]]
