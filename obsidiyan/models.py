"""Datenmodell des Corpus.

Ein Doc ist eine Konversation aus genau einer Quelle. Die Provenance-Felder sind
nicht Deko: Datum, Rolle und Projekt sind das Retrieval-Signal, weil der Corpus
per grep durchsucht wird und keinen Index hat.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Role(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class Sensitivity(StrEnum):
    """Freigabestatus eines Docs. Siehe obsidiyan.nda fuer die Klassifikation.

    COPYRIGHT ist die dritte Stufe fuer fremdes, urheberrechtlich geschuetztes
    Material (Kurse, Buecher). Es ist absichtlich NICHT wie NDA versteckt: es
    soll auffindbar sein. Es darf aber weder destilliert noch committed werden,
    deshalb reicht CLEAN nicht.
    """

    CLEAN = "clean"
    COPYRIGHT = "copyright"
    NDA = "nda"


class Source(StrEnum):
    CLAUDE_CODE = "claude-code"
    CODEX = "codex"
    ANTIGRAVITY = "antigravity"
    CHATGPT = "chatgpt"
    GEMINI = "gemini"
    CLAUDE_WEB = "claude-web"
    MEMORY = "memory"
    BOOK = "book"
    COURSE = "course"


class Turn(BaseModel):
    """Ein Redebeitrag.

    `machine_summary` trennt drei Beweisklassen, die sonst kollabieren:
      user, machine_summary=False -> des Nutzers eigene Worte, primaere Quelle
      user, machine_summary=True  -> Harness-Zusammenfassung SEINER Entscheidungen,
                                     sekundaer: inhaltlich echt, aber nicht woertlich
      assistant                   -> Modell-Output, keine Personenquelle
    """

    model_config = ConfigDict(frozen=True)

    role: Role
    ts: datetime | None = None
    text: str
    machine_summary: bool = False


class Doc(BaseModel):
    """Eine normalisierte Konversation."""

    model_config = ConfigDict(frozen=True)

    source: Source
    conv_id: str
    title: str = ""
    project: str = ""
    started_at: datetime | None = None
    ended_at: datetime | None = None
    sensitivity: Sensitivity = Sensitivity.NDA
    cwd: str = ""
    git_branch: str = ""
    turns: tuple[Turn, ...] = Field(default_factory=tuple)

    @property
    def is_empty(self) -> bool:
        return not any(t.text.strip() for t in self.turns)

    @property
    def deduped_turns(self) -> tuple[Turn, ...]:
        """Wortgleiche Wiederholungen entfernen.

        Der claude.ai-Export enthaelt denselben Turn mehrfach (Regenerierungen,
        Branch-Reste): in einem geprueften Dokument 15 von 54. Ohne Dedup blaehen
        sie Zeichenzahlen auf und erzeugen doppelte Claims.
        """
        seen: set[tuple[str, str]] = set()
        out: list[Turn] = []
        for turn in self.turns:
            ident = (turn.role.value, turn.text.strip())
            if ident in seen:
                continue
            seen.add(ident)
            out.append(turn)
        return tuple(out)

    @property
    def user_text(self) -> str:
        """Nur woertliche Nutzer-Turns. Autoritative Personenquelle laut BRAIN.md."""
        return "\n\n".join(
            t.text for t in self.deduped_turns if t.role is Role.USER and not t.machine_summary
        )
