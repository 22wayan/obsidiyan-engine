"""Kandidatenauswahl, rein deterministisch und ohne Modell.

Messung vom 08.08.2026: von 2.912 clean Docs haben nur 356 mehr als 2.000 Zeichen
Nutzertext. Der Rest sind Wegwerf-Fragen, ueberwiegend Gemini-Einzelaustausche.
Sie durch ein Modell zu schicken waere Geldverbrennung, deshalb schneidet diese
Stufe sie weg, bevor Kosten entstehen.

Gewertet wird ausschliesslich NUTZER-Text. Assistant-Antworten sind laut
BRAIN.md keine autoritative Personenquelle und deshalb kein Signal fuer
Relevanz.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from obsidiyan.corpusio import iter_docs, read_doc
from obsidiyan.distill.prose import prose_ratio
from obsidiyan.models import Doc, Role, Sensitivity, Source

MIN_USER_CHARS = 2000
MIN_PROSE_RATIO = 0.45
RECENCY_HALFLIFE_DAYS = 365.0
_AUTOMATION_TAG_RE = re.compile(r"^<(?:scheduled|automated)-task(?:\s|>)", re.IGNORECASE)


@dataclass(frozen=True)
class Candidate:
    doc_id: str
    path: Path
    project: str
    source: str
    started_at: date | None
    user_chars: int
    prose: float
    score: float
    source_fingerprint: str
    sensitivity: Sensitivity = Sensitivity.CLEAN

    def render(self) -> str:
        day = self.started_at.isoformat() if self.started_at else "????-??-??"
        flag = " [NDA]" if self.sensitivity is Sensitivity.NDA else ""
        return (
            f"{self.score:9.0f}  {day}  {self.source:12} "
            f"{self.user_chars:7} Z  Prosa {self.prose:.0%}  {self.project[:28]}{flag}\n"
            f"           {self.doc_id}"
        )


def user_chars(doc: Doc) -> int:
    """Nur woertlicher Nutzertext. Maschinen-Zusammenfassungen zaehlen halb,
    weil sie Entscheidungen protokollieren, aber nicht des Nutzers Formulierung sind."""
    total = 0.0
    for turn in doc.deduped_turns:
        if turn.role is not Role.USER:
            continue
        total += len(turn.text) * (0.5 if turn.machine_summary else 1.0)
    return int(total)


def source_fingerprint(doc: Doc) -> str:
    """Stabile Revision des autoritativen Nutzerinhalts einer Quelle.

    Provider behalten ihre Conversation-ID, wenn ein Chat spaeter fortgesetzt
    oder mit einem neuen Export aktualisiert wird. Der Corpus-Pfad bleibt dann
    gleich. Der Fingerprint sorgt dafuer, dass eine alte Review-Markierung den
    neuen Inhalt nicht faelschlich als bereits abgeschlossen behandelt.

    Der Zeitstempel gehoert bewusst NICHT dazu. Bei Memory-Snapshots ohne
    created/modified faellt er auf die Dateizeit zurueck, und die schreibt
    bin/sync-memory.sh bei jedem Refresh neu. Am 2026-09-01 hat das 26 laengst
    destillierte Quellen zurueck in die Warteschlange geworfen, ohne dass sich
    ein Zeichen Nutzertext geaendert haette. Ein Metadatum darf keine
    Inhaltsrevision melden.
    """
    rows = [
        {
            "machine_summary": turn.machine_summary,
            "text": turn.text,
        }
        for turn in doc.deduped_turns
        if turn.role is Role.USER
    ]
    payload = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _recency(day: date | None, today: date) -> float:
    if day is None:
        return 0.5
    age = max((today - day).days, 0)
    return float(0.5 ** (age / RECENCY_HALFLIFE_DAYS))


def _is_distillable(doc: Doc) -> bool:
    """Nur Primaerquellen, keine eigenen Destillate oder Automation-Prompts.

    Kuratierte ``notes/`` und ``private/`` werden als Memory in den Such-Corpus
    gespiegelt. Sie erneut zu Claims zu verarbeiten waere zirkulaere Provenienz. Scheduled Tasks
    erscheinen technisch als Nutzer-Turn, wurden aber ohne anwesenden Nutzer
    erzeugt und sind deshalb ebenfalls keine Personenquelle.
    """
    if doc.source is Source.MEMORY and doc.project in ("notes", "private"):
        return False
    return not any(
        turn.role is Role.USER and _AUTOMATION_TAG_RE.match(turn.text.lstrip())
        for turn in doc.turns
    )


def select(
    corpus_root: Path,
    *,
    limit: int | None = None,
    min_user_chars: int = MIN_USER_CHARS,
    project: str | None = None,
    today: date | None = None,
) -> list[Candidate]:
    """Die Dokumente, aus denen sich Destillieren lohnt, dichteste zuerst."""
    now = today or date.today()
    out: list[Candidate] = []

    for path in iter_docs(corpus_root):
        try:
            doc = read_doc(path)
        except (ValueError, KeyError):
            continue
        if doc.sensitivity is Sensitivity.COPYRIGHT:
            # Fremdes Kursmaterial ist nicht des Nutzers Wissen und darf auch als
            # Zusammenfassung nirgendwo als eigenes auftauchen. NDA dagegen ist
            # eigenes Wissen und wird destilliert; das Destillat landet in
            # private/, siehe obsidiyan.distill.route.
            continue
        if not _is_distillable(doc):
            continue
        if project and project.lower() not in doc.project.lower():
            continue

        chars = user_chars(doc)
        if chars < min_user_chars:
            continue

        prose = prose_ratio(doc.user_text)
        if prose < MIN_PROSE_RATIO:
            continue  # eingefuegte Logs und Dumps, kein persoenliches Wissen

        day = doc.started_at.date() if doc.started_at else None
        out.append(
            Candidate(
                doc_id=str(path.relative_to(corpus_root)),
                path=path,
                project=doc.project,
                source=doc.source.value,
                started_at=day,
                user_chars=chars,
                prose=prose,
                score=chars * prose * _recency(day, now),
                source_fingerprint=source_fingerprint(doc),
                sensitivity=doc.sensitivity,
            )
        )

    out.sort(key=lambda c: c.score, reverse=True)
    return out[:limit] if limit else out


def pending(corpus_root: Path, done: set[str], **kw: object) -> list[Candidate]:
    """Kandidaten, die noch nicht extrahiert wurden.

    ``done`` wird aus Claim- und Review-Store gebildet: Eine Quelle ist damit
    entweder in belegtes Wissen ueberfuehrt oder nach Volltextpruefung bewusst
    ohne dauerhafte Claims abgeschlossen.
    """
    return [c for c in select(corpus_root, **kw) if c.doc_id not in done]  # type: ignore[arg-type]


def user_text(path: Path, *, max_chars: int = 40_000) -> str:
    """Nur die Nutzer-Turns eines Docs, fuer die Extraktion aufbereitet."""
    doc = read_doc(path)
    parts = [
        f"[{t.ts.date().isoformat() if t.ts else '????-??-??'}]"
        f"{' [MASCHINELLE ZUSAMMENFASSUNG]' if t.machine_summary else ''} {t.text}"
        for t in doc.deduped_turns
        if t.role is Role.USER and t.text.strip()
    ]
    joined = "\n\n".join(parts)
    return joined[:max_chars]
