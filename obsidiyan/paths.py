"""Wo der Vault liegt, den dieses Paket bedient.

Bisher galt implizit: es gibt genau eine Kopie des Pakets, und der Vault liegt
immer genau eine Ebene darueber. Beide Annahmen halten nicht mehr.

1. Ein Conductor-Worktree enthaelt eine zweite Kopie von ``obsidiyan/``, aber
   kein ``corpus/`` (das ist gitignored). Startet der MCP-Server mit dem
   Worktree als CWD, gewinnt diese Kopie beim Import ueber ``-m``, und der
   Server durchsucht ein Verzeichnis, das es nicht gibt. Er lieferte dann
   stillschweigend null Treffer statt eines Fehlers.
2. Dieselbe Engine soll einen zweiten Vault bedienen koennen (Team-Vault), ohne
   dass der Code dupliziert wird.

Deshalb entscheidet ``OBSIDIYAN_HOME`` und nicht die Paket-Position. Ohne die
Variable bleibt das alte Verhalten erhalten, damit bestehende Aufrufe aus dem
Repo heraus unveraendert funktionieren.
"""

from __future__ import annotations

import os
from pathlib import Path

ENV_VAR = "OBSIDIYAN_HOME"

PACKAGE_PARENT = Path(__file__).resolve().parent.parent
"""Fallback: das Verzeichnis ueber dem Paket, also das Repo der eigenen Kopie."""


class VaultError(RuntimeError):
    """Der konfigurierte Vault existiert nicht oder ist unvollstaendig."""


def vault_root() -> Path:
    """Wurzel des Vaults. ``OBSIDIYAN_HOME`` gewinnt gegen die Paket-Position."""
    configured = os.environ.get(ENV_VAR, "").strip()
    if not configured:
        return PACKAGE_PARENT
    root = Path(configured).expanduser().resolve()
    if not root.is_dir():
        raise VaultError(
            f"{ENV_VAR}={configured!r} is not a directory: {root}. "
            "Create the vault with 'python -m obsidiyan init <path>'."
        )
    return root


def corpus_root() -> Path:
    return vault_root() / "corpus"


def notes_root() -> Path:
    return vault_root() / "notes"


def private_root() -> Path:
    return vault_root() / "private"


def chats_root() -> Path:
    return vault_root() / "chats"


def distill_root() -> Path:
    return vault_root() / "distill"


def source_overrides_path() -> Path:
    return private_root() / "nda-source-overrides.json"


def require_corpus() -> Path:
    """Corpus-Pfad, aber laut statt leer, wenn er fehlt.

    Ein fehlender Corpus ist entweder ein frischer Vault (Refresh noch nie
    gelaufen) oder eine falsch aufgeloeste Wurzel. Beides ist behebbar, aber nur
    wenn der Aufrufer es erfaehrt. Null Treffer sehen dagegen aus wie ein
    leerer Wissensstand.
    """
    corpus = corpus_root()
    if not corpus.is_dir():
        configured = os.environ.get(ENV_VAR) or f"not set, falling back to {PACKAGE_PARENT}"
        raise VaultError(
            f"No corpus under {corpus}. "
            f"Point {ENV_VAR} at your vault (currently: {configured}) "
            f"or run 'python -m obsidiyan init' and an ingest first."
        )
    return corpus
