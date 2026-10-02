"""Welches Destillat in welchen Layer geht.

Bis zum 24.08.2026 wurde NDA-Material gar nicht destilliert. Der Grund war
richtig, aber die Schlussfolgerung zu grob: `notes/` ist git-getrackt, also
durfte dort nichts Vertrauliches landen. Uebersehen wurde, dass es einen
zweiten, gitignorierten Layer gibt. Vertrauliches Wissen blieb dadurch als
Rohtranskript im Corpus liegen und wurde nie zu einer Note, obwohl genau das
der Zweck des Brains ist.

Ab jetzt gilt: **destilliert wird beides, geschrieben wird getrennt.**

- Claim aus einer `clean`-Quelle  -> `notes/`, git-getrackt
- Claim aus einer `nda`-Quelle    -> `private/`, gitignored
- Claim aus einer `copyright`-Quelle gibt es nicht, die filtert `select` weg

Die Stufe wird **aus dem Corpus gelesen, nicht vom extrahierenden Agenten
uebernommen**. Ein Agent, der `sensitivity` selbst setzen duerfte, koennte sich
vertippen und damit die Layer-Grenze oeffnen. Dieselbe Logik wie beim
Kurs-Layer: der Layer entscheidet, nicht das Frontmatter.

Ein Thema darf seine Stufe nicht mischen. Eine Note kann nicht halb privat
sein, und ein Thema, das spaeter von clean nach nda kippt, wuerde eine
verwaiste Note in `notes/` zuruecklassen. Gemischte Themen sind deshalb ein
Fehler mit Ansage, kein stilles Zusammenfuehren.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from obsidiyan.corpusio import read_doc
from obsidiyan.distill.models import Claim
from obsidiyan.models import Sensitivity
from obsidiyan.provenance import resolve_doc_id


@dataclass(frozen=True)
class Routed:
    """Claims, aufgeteilt nach Ziel-Layer."""

    clean: list[Claim]
    private: list[Claim]


class RoutingError(ValueError):
    """Ein Thema mischt Stufen oder eine Quelle ist nicht auflösbar."""


def source_sensitivity(claim: Claim, corpus_root: Path) -> Sensitivity:
    """Die Stufe der Quelle, aus der der Claim stammt."""
    return doc_sensitivity(claim.source_doc_id, corpus_root)


def doc_sensitivity(doc_id: str, corpus_root: Path) -> Sensitivity:
    """Die Stufe eines Corpus-Dokuments, an seinem relativen Pfad."""
    path = resolve_doc_id(doc_id, corpus_root)
    if path is None:
        raise RoutingError(f"Quelle fehlt im Corpus: {doc_id}")
    try:
        return read_doc(path).sensitivity
    except (OSError, ValueError, KeyError) as exc:
        raise RoutingError(f"Quelle unlesbar: {doc_id}: {exc}") from exc


def split_doc_ids(doc_ids: list[str], corpus_root: Path) -> tuple[list[str], list[str]]:
    """Teilt Doc-IDs in clean und nda. Fuer Reviews, die keine Claims tragen."""
    clean: list[str] = []
    private: list[str] = []
    for doc_id in doc_ids:
        level = doc_sensitivity(doc_id, corpus_root)
        if level is Sensitivity.COPYRIGHT:
            raise RoutingError(f"Review auf urheberrechtlich geschuetzte Quelle: {doc_id}")
        (private if level is Sensitivity.NDA else clean).append(doc_id)
    return clean, private


def route(claims: list[Claim], corpus_root: Path) -> Routed:
    """Teilt Claims nach der Stufe ihrer Quelle auf, Thema fuer Thema."""
    levels: dict[str, Sensitivity] = {}
    per_claim: list[tuple[Claim, Sensitivity]] = []

    for claim in claims:
        level = source_sensitivity(claim, corpus_root)
        if level is Sensitivity.COPYRIGHT:
            raise RoutingError(
                f"Claim aus urheberrechtlich geschuetzter Quelle: {claim.source_doc_id}"
            )
        previous = levels.setdefault(claim.topic, level)
        if previous is not level:
            raise RoutingError(
                f"{claim.topic!r} mischt {previous.value} und {level.value}. "
                "Eine Note kann nicht halb privat sein: fuer den vertraulichen "
                "Teil ein eigenes Thema waehlen."
            )
        per_claim.append((claim, level))

    return Routed(
        clean=[claim for claim, level in per_claim if level is Sensitivity.CLEAN],
        private=[claim for claim, level in per_claim if level is Sensitivity.NDA],
    )
