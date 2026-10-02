#!/usr/bin/env python
"""Gibt die Claims aus, die im Arbeitsbaum stehen, aber noch nicht im Vergleichsstand.

Zweck: die unabhaengige Pruefung soll genau das ansehen, was ein
Destillationslauf neu behauptet hat, nicht den ganzen gewachsenen Store.
Vergleichsstand ist per Default origin/main.

Der private Store ist gitignored und hat deshalb keinen Vergleichsstand in git.
Ohne Schnappschuss war er fuer die Pruefung unsichtbar: ein Lauf, der nur
NDA-Quellen destillierte, meldete "0 neue Claims geprueft, 0 Probleme" und die
Pruefinstanz hatte nichts gesehen. Der zweite Parameter nimmt deshalb eine vor
dem Lauf gezogene Kopie entgegen.

    new-claims.py [ref] [privater-schnappschuss.json]
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

STORE = Path("distill/claims.json")
PRIVATE_STORE = Path("distill/claims-private.json")


def _identity(claim: dict) -> tuple:
    """Ein Claim ist derselbe, wenn Quelle, Sachverhalt und Aussage gleich sind."""
    return (
        claim.get("source_doc_id", ""),
        claim.get("key", ""),
        claim.get("text", ""),
    )


def _baseline(ref: str) -> list[dict]:
    proc = subprocess.run(
        ["git", "show", f"{ref}:{STORE}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return []
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return []


def _load(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return rows if isinstance(rows, list) else []


def main() -> int:
    ref = sys.argv[1] if len(sys.argv) > 1 else "origin/main"
    snapshot = Path(sys.argv[2]) if len(sys.argv) > 2 else None

    known = {_identity(c) for c in _baseline(ref)}
    fresh = [c for c in _load(STORE) if _identity(c) not in known]

    # Der private Store wird gegen den Schnappschuss verglichen. Fehlt er, wird
    # ALLES darin als neu ausgegeben statt nichts: die Pruefung soll im Zweifel
    # zu viel sehen, nicht zu wenig.
    private_known = {_identity(c) for c in _load(snapshot)} if snapshot else set()
    fresh += [c for c in _load(PRIVATE_STORE) if _identity(c) not in private_known]

    print(json.dumps(fresh, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
