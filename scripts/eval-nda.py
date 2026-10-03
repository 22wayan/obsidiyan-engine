#!/usr/bin/env python3
"""Misst den NDA-Klassifizierer an einem beschrifteten Fallsatz.

Usage: .venv/bin/python scripts/eval-nda.py [--strict]

Der Fallsatz in evals/nda_cases.jsonl enthaelt bewusst auch vertrauliche Texte
ohne bekannten Namen. Die erkennt eine Regel-Liste nicht, dafuer gibt es die
Overrides. Die Zahl soll ehrlich sein, nicht 100 Prozent.

Secrets stehen im Fallsatz nur als Platzhalter und werden erst hier
zusammengesetzt, damit Secret-Scanner das Repo nicht anschlagen.
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from obsidiyan.models import Sensitivity  # noqa: E402
from obsidiyan.nda import classify  # noqa: E402

CASES = ROOT / "evals" / "nda_cases.jsonl"

FAKE_SECRETS = {
    "{ANTHROPIC}": "sk-" + "ant-" + "x" * 32,
    "{OPENAI}": "sk-" + "proj" + "Q" * 24,
    "{GITHUB}": "gh" + "p_" + "A1b2" * 9,
    "{AWS}": "AK" + "IA" + "Z" * 16,
    "{GOOGLE}": "AI" + "za" + "x" * 35,
    "{PRIVATE_KEY}": "-----BEGIN RSA " + "PRIVATE KEY-----",
    "{KEY_21ST}": "an_" + "sk_" + "a1B2c3D4" * 4,
    "{STRIPE_LIVE}": "sk_" + "live_" + "Z9y8X7w6" * 3,
    "{GENERIC}": "q7Lm2Xr9" + "Tb4Vn8Kc1Hp6Wd3Zs5",
}

# Untergrenzen fuer CI. Sinkt ein Wert, hat eine Aenderung den Klassifizierer
# verschlechtert. Bewusst nicht 1.0: siehe Modul-Docstring.
MIN_RECALL = 0.85
MIN_PRECISION = 0.95


def _text(raw: str) -> str:
    for placeholder, value in FAKE_SECRETS.items():
        raw = raw.replace(placeholder, value)
    return raw


def main(strict: bool) -> int:
    rows = [json.loads(line) for line in CASES.read_text(encoding="utf-8").splitlines() if line]
    counts: Counter[str] = Counter()
    per_category: dict[str, Counter[str]] = defaultdict(Counter)
    misses: list[str] = []

    for row in rows:
        predicted = classify(row["project"], _text(row["text"]))
        is_nda = predicted is Sensitivity.NDA
        expected_nda = row["expected"] == "nda"
        outcome = {
            (True, True): "tp",
            (False, False): "tn",
            (True, False): "fp",
            (False, True): "fn",
        }[(is_nda, expected_nda)]
        counts[outcome] += 1
        per_category[row["category"]]["ok" if outcome in ("tp", "tn") else "wrong"] += 1
        if outcome in ("fp", "fn"):
            misses.append(f"  {outcome.upper()} {row['id']:11} {row['text'][:70]}")

    recall = counts["tp"] / (counts["tp"] + counts["fn"])
    precision = counts["tp"] / (counts["tp"] + counts["fp"])
    print(f"cases={len(rows)} confidential={counts['tp'] + counts['fn']} "
          f"clean={counts['tn'] + counts['fp']}")
    print(f"recall={recall:.2f} precision={precision:.2f} "
          f"false_positives={counts['fp']} false_negatives={counts['fn']}")
    print("\nper category:")
    for category, c in sorted(per_category.items()):
        total = c["ok"] + c["wrong"]
        print(f"  {category:30} {c['ok']:2}/{total}")
    if misses:
        print("\nmisses:")
        print("\n".join(misses))

    if strict and (recall < MIN_RECALL or precision < MIN_PRECISION):
        print(f"\nFAIL: below recall {MIN_RECALL} or precision {MIN_PRECISION}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main("--strict" in sys.argv[1:]))
