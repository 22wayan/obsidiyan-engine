#!/usr/bin/env python3
"""Misst, ob die Suche die richtige Session findet.

Usage: .venv/bin/python scripts/eval-search.py [--strict]

Baut einen frischen Vault aus der fiktiven Ledgerly-Historie in
evals/agent/history und stellt fuer jede Frage aus evals/agent/questions.json
die Suchanfrage, die ein Agent typischerweise tippen wuerde. Gemessen wird,
ob die Session mit der Antwort unter den ersten Treffern steht. Der Lauf ist
deterministisch und braucht kein Modell, deshalb laeuft er in CI.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVAL = ROOT / "evals" / "agent"
TODAY = date(2026, 7, 20)
MIN_HIT_AT_3 = 0.85


def main(strict: bool) -> int:
    vault = Path(tempfile.mkdtemp(prefix="obsidiyan-eval-")) / "vault"
    vault.mkdir(parents=True)
    os.environ["OBSIDIYAN_HOME"] = str(vault)
    sys.path.insert(0, str(ROOT))

    from obsidiyan.ingest import run as ingest_run
    from obsidiyan.search import search
    from obsidiyan.vault_init import init_vault

    init_vault(vault, today=TODAY)
    ingest_run(
        "claude-code",
        vault / "corpus",
        session_root=EVAL / "history",
        source_overrides_path=vault / "private" / "nda-source-overrides.json",
        require_source_overrides=False,
    )

    questions = json.loads((EVAL / "questions.json").read_text(encoding="utf-8"))
    hit1 = hit3 = 0
    rr = 0.0
    misses = []
    for q in questions:
        hits = search(q["query"], vault / "corpus", limit=10, today=TODAY)
        ranks = [i + 1 for i, h in enumerate(hits) if h.doc.conv_id in q["gold"]]
        first = ranks[0] if ranks else None
        hit1 += first == 1
        hit3 += bool(first and first <= 3)
        rr += 1 / first if first else 0.0
        if not first or first > 1:
            misses.append(f"  {q['id']} query={q['query']!r} gold rank={first}")

    n = len(questions)
    print(f"questions={n} hit@1={hit1 / n:.2f} hit@3={hit3 / n:.2f} mrr={rr / n:.2f}")
    if misses:
        print("not ranked first:")
        print("\n".join(misses))
    if strict and hit3 / n < MIN_HIT_AT_3:
        print(f"FAIL: hit@3 below {MIN_HIT_AT_3}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main("--strict" in sys.argv[1:]))
