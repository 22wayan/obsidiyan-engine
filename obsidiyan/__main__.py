"""Entry point: python -m obsidiyan"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _create_vault_dir_for_init(argv: list[str]) -> None:
    """``init`` darf ein OBSIDIYAN_HOME anlegen, das es noch nicht gibt.

    Mehrere Module loesen den Vault schon beim Import auf und brechen bei einem
    fehlenden Verzeichnis ab. Ohne diesen Schritt scheitert ausgerechnet der
    Befehl, der den Vault erst erzeugen soll.
    """
    configured = os.environ.get("OBSIDIYAN_HOME", "").strip()
    if "init" in argv and configured:
        Path(configured).expanduser().mkdir(parents=True, exist_ok=True)


if __name__ == "__main__":
    _create_vault_dir_for_init(sys.argv[1:])
    from obsidiyan.paths import VaultError

    try:
        from obsidiyan.cli import main

        sys.exit(main())
    except VaultError as exc:
        print(f"obsidiyan: {exc}", file=sys.stderr)
        sys.exit(2)
