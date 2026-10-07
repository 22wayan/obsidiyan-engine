#!/usr/bin/env python3
"""Bewertet gespeicherte Agent-Eval-Ergebnisse mit den aktuellen Regeln neu.

Usage: python evals/agent/regrade.py evals/agent/results/*.json
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_agent_eval import grade

EVAL = Path(__file__).resolve().parent


def main(paths: list[str]) -> int:
    questions = {q["id"]: q for q in json.loads((EVAL / "questions.json").read_text())}
    totals: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    for name in paths:
        rows = json.loads(Path(name).read_text(encoding="utf-8"))
        for row in rows:
            q = questions[row["id"]]
            new = grade(row["answer"], q["must"], q.get("reject"))
            key = f"{Path(name).stem.rsplit('-run', 1)[0]} {row['condition']}"
            totals[key][0] += new
            totals[key][1] += 1
            totals[key][2] += new != row["correct"]
    for key, (ok, n, changed) in sorted(totals.items()):
        print(f"{key:48} {ok}/{n}  ({changed} regraded)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
