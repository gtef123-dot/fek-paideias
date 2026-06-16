"""Event detection for amendments/repeals (instead of modelling global state).

A repeal/amendment is an EVENT published in a ΦΕΚ we already ingest: the text
says «καταργείται/αντικαθίσταται/τροποποιείται … το ν. ΥΥΥΥ/ΖΖΖΖ». We do NOT try
to know whether a law is "in force" (intractable). We only detect, on the stream
we already have, that an incoming act CHANGES a law we track — and flag it for
human review, with a link to the amending ΦΕΚ.
"""
from __future__ import annotations

import re

from .taxonomy import normalize

# Change verbs (matched on accent-stripped text).
_CHANGE = re.compile(r"καταργ|αντικαθιστ|αντικατασταθ|αντικαταστασ|τροποποι|αναριθμ")

# Law/act reference -> (number, year). Matches "ν. 4823/2021", "4823/2021",
# "109697/Δ2/2024", "491/Β/2021" (optional τεύχος/service code in the middle).
_REF = re.compile(r"(\d{2,6})\s*/\s*(?:[Α-Ωα-ωΆ-Ώά-ώA-Za-z0-9.]{1,6}\s*/\s*)?(19|20)(\d{2})")


def law_keys(text: str) -> set[tuple[str, str]]:
    """All (number, year) reference keys mentioned in the text."""
    return {(m.group(1), m.group(2) + m.group(3)) for m in _REF.finditer(text or "")}


def primary_law_key(text: str) -> tuple[str, str] | None:
    """The act's OWN identity = the FIRST reference (titles read 'Νόμος 4823/2021
    — …'). Avoids tracking laws merely CITED in the title (e.g. amendments)."""
    m = _REF.search(text or "")
    return (m.group(1), m.group(2) + m.group(3)) if m else None


def change_targets(text: str) -> set[tuple[str, str]]:
    """Law keys this text announces a CHANGE to (empty unless a change verb appears)."""
    if not text or not _CHANGE.search(normalize(text)):
        return set()
    return law_keys(text)
