#!/usr/bin/env bash
# check.sh — read-only Qualitaetsgate. Idempotent, mutiert nichts.
# Laeuft ruff, mypy und pytest. Exit 0 nur wenn alles gruen ist.
# Graph- und NDA-Gate pruefen einen Vault, nicht den Code: scripts/verify-graph.py
# und scripts/verify-nda.py mit gesetztem OBSIDIYAN_HOME.
set -euo pipefail
cd "$(dirname "$0")/.."

VENV=".venv/bin"
[ -x "$VENV/python" ] || { echo "check: .venv fehlt, erst 'python3.12 -m venv .venv' laufen lassen" >&2; exit 1; }

echo "== ruff =="
"$VENV/ruff" check obsidiyan tests
echo "== mypy =="
"$VENV/mypy" obsidiyan
echo "== pytest =="
"$VENV/pytest"
