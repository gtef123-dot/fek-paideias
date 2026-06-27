"""Crash-safe checkpointing for long, expensive (agent/credit-bound) passes.

The daily ingest is already resumable at the SQLite level (every insert commits
to fek.db on disk). But two things are NOT durable until run.py finishes:
  1. the sharded JSON under docs/data/ (written only by store.export at the end),
  2. any multi-hour harvest/verify work that hasn't reached export yet.

So a large agent-driven download that runs out of credits / is killed mid-way
would lose everything since the last full run. This module fixes that with:

  * atomic_write_json — temp-file + os.replace, so a kill mid-write never leaves
    a half-written (corrupt) file; the previous good file stays intact.
  * Ledger — a tiny resumable work-queue persisted to a git-committed JSON file.
    Each item carries a status (pending/done/failed/needs_human) and is flushed
    to disk the moment it changes, so a re-run skips finished work and continues.

Nothing here knows about laws/verification specifically — it's the safety
primitive that harvest.py / verify.py build on.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Iterable


def atomic_write_json(path: Path, obj) -> None:
    """Write JSON so an interrupted process never corrupts the existing file.

    Writes to a temp file in the SAME directory (so os.replace is atomic on the
    same filesystem) and only then atomically swaps it into place.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=1)
            fh.flush()
            os.fsync(fh.fileno())          # force to disk before the swap
        os.replace(tmp, path)              # atomic on POSIX and Windows
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


# Item statuses.
PENDING = "pending"
DONE = "done"
FAILED = "failed"          # tried, hard-failed (e.g. bad PDF) — counts attempts
NEEDS_HUMAN = "needs_human"  # AI flagged uncertain/contradictory — exception queue


class Ledger:
    """Resumable, disk-backed work-queue keyed by a stable item id.

    On every state change the whole ledger is atomically rewritten, so progress
    survives a credit-out / crash. Re-running the same pass loads the ledger,
    skips DONE/exhausted items, and processes only what's left.

    Each entry: {id, status, attempts, data, error}. `data` holds whatever the
    pass wants to remember (the harvested candidate, the verification verdict…).
    """

    def __init__(self, path: Path, *, max_attempts: int = 2) -> None:
        self.path = Path(path)
        self.max_attempts = max_attempts
        self.items: dict[str, dict] = {}
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                self.items = {it["id"]: it for it in raw.get("items", []) if it.get("id")}
            except Exception:  # noqa: BLE001 — a corrupt ledger shouldn't crash the run
                self.items = {}

    # ── persistence ─────────────────────────────────────────────────────────
    def flush(self) -> None:
        atomic_write_json(self.path, {
            "count": len(self.items),
            "done": sum(1 for it in self.items.values() if it["status"] == DONE),
            "items": list(self.items.values()),
        })

    # ── queue mechanics ───────────────────────────────────────────────────────
    def add(self, item_id: str, data: dict | None = None) -> None:
        """Register an item if unseen (idempotent — keeps existing progress)."""
        if item_id not in self.items:
            self.items[item_id] = {"id": item_id, "status": PENDING,
                                   "attempts": 0, "data": data or {}, "error": None}

    def add_many(self, ids_with_data: Iterable[tuple[str, dict]]) -> int:
        before = len(self.items)
        for item_id, data in ids_with_data:
            self.add(item_id, data)
        added = len(self.items) - before
        if added:
            self.flush()
        return added

    def is_settled(self, item_id: str) -> bool:
        """True if this item needs no more work (done, escalated, or out of tries)."""
        it = self.items.get(item_id)
        if not it:
            return False
        if it["status"] in (DONE, NEEDS_HUMAN):
            return True
        return it["status"] == FAILED and it["attempts"] >= self.max_attempts

    def pending_ids(self) -> list[str]:
        return [i for i in self.items if not self.is_settled(i)]

    def get(self, item_id: str) -> dict | None:
        return self.items.get(item_id)

    def bump_attempt(self, item_id: str) -> None:
        """Count an attempt BEFORE doing the risky work, so a crash mid-item is
        recorded — a permanently-bad item won't be retried forever."""
        it = self.items.get(item_id)
        if it:
            it["attempts"] += 1
            self.flush()

    def mark(self, item_id: str, status: str, *, data: dict | None = None,
             error: str | None = None) -> None:
        """Set an item's outcome and immediately persist (the checkpoint)."""
        it = self.items.get(item_id)
        if it is None:
            it = {"id": item_id, "status": PENDING, "attempts": 0, "data": {}, "error": None}
            self.items[item_id] = it
        it["status"] = status
        if data is not None:
            it["data"] = data
        it["error"] = error
        self.flush()

    # ── reporting ──────────────────────────────────────────────────────────────
    def summary(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for it in self.items.values():
            out[it["status"]] = out.get(it["status"], 0) + 1
        out["total"] = len(self.items)
        return out
