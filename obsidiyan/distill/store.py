"""Persistenter Claim-Speicher.

Der Grund fuer diese Datei: eine Note aus einem Dokument ist eine
Zusammenfassung. Wissen entsteht erst, wenn dasselbe Thema aus vielen Gespraechen
ueber Jahre zusammenlaeuft und Widersprueche nach Datum aufgeloest werden.

Dafuer muss die Extraktion ueber Laeufe UND ueber Sessions hinweg ansammeln
koennen, statt pro Durchlauf eine Note zu ueberschreiben. Der Store ist die
Zwischenschicht: Extraktion schreibt hier hinein, Emission liest hier heraus.

Idempotent: derselbe Claim aus derselben Quelle wird nicht doppelt gespeichert.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from obsidiyan import paths
from obsidiyan.distill.models import Claim, SourceReview
from obsidiyan.provenance import doc_signature

DEFAULT_PATH = paths.distill_root() / "claims.json"
DEFAULT_REVIEW_PATH = paths.distill_root() / "reviews.json"
DEFAULT_PRIVATE_PATH = paths.distill_root() / "claims-private.json"
DEFAULT_PRIVATE_REVIEW_PATH = paths.distill_root() / "reviews-private.json"
"""Zweiter Satz Stores fuer Claims aus NDA-Quellen, gitignored.

Seit NDA destilliert wird, traegt ein Claim moeglicherweise vertraulichen Text.
Der Auto-Distill-Lauf macht `git add distill` und oeffnet einen PR; ein
gemeinsamer Store waere damit der direkte Weg, Kundenwissen nach GitHub zu
schieben. Die Trennung ist dieselbe wie zwischen notes/ und private/: der
git-getrackte Store enthaelt ausschliesslich Claims aus clean-Quellen, und
scripts/verify-nda.py prueft genau das.
"""
"""Bewusst NICHT unter corpus/.

corpus/ ist gitignored und in Sekunden deterministisch neu baubar. Claims sind
das Gegenteil: sie kosten Extraktion, sind nicht reproduzierbar und tragen die
Provenienz. Sie gehoeren versioniert.
"""


class ClaimStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._claims: dict[str, Claim] = {}
        if path.exists():
            for row in json.loads(path.read_text(encoding="utf-8")):
                claim = Claim.model_validate(row)
                self._claims[self._identity(claim)] = claim

    @staticmethod
    def _identity(claim: Claim) -> str:
        """Thema, Sachverhalt, Quelle UND Datum.

        Das Datum muss mit hinein: ein Dokument kann sich ueber Tage ziehen und
        denselben Sachverhalt zweimal unterschiedlich festlegen (Addon erst 190,
        drei Tage spaeter 149). Ohne Datum in der Identitaet kollabieren beide
        und genau der Widerspruch geht verloren, den die Aufloesung zeigen soll.

        Bewusst NICHT der Text: eine praezisere Formulierung desselben Tages aus
        derselben Quelle ersetzt die aeltere, statt danebenzustehen.
        """
        tag = claim.stated_on.isoformat() if claim.stated_on else "ohne-datum"
        return f"{claim.topic}\x00{claim.key}\x00{claim.source_doc_id}\x00{tag}"

    def add(self, claims: list[Claim]) -> tuple[int, int]:
        """Rueckgabe: (neu, ersetzt)."""
        new = replaced = 0
        for claim in claims:
            ident = self._identity(claim)
            if ident in self._claims:
                replaced += 1
            else:
                new += 1
            self._claims[ident] = claim
        return new, replaced

    def all(self) -> list[Claim]:
        return sorted(self._claims.values(), key=lambda c: (c.topic, c.key, c.source_doc_id))

    def topics(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for claim in self._claims.values():
            out[claim.topic] = out.get(claim.topic, 0) + 1
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))

    def sources(self) -> set[str]:
        return {c.source_doc_id for c in self._claims.values()}

    def remove_sources(self, doc_ids: set[str]) -> int:
        """Entfernt Claims, deren Provenienz nicht mehr clean oder vorhanden ist."""
        identities = [
            identity
            for identity, claim in self._claims.items()
            if claim.source_doc_id in doc_ids
        ]
        for identity in identities:
            del self._claims[identity]
        return len(identities)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        rows = [json.loads(c.model_dump_json()) for c in self.all()]
        self.path.write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding="utf-8")


class ReviewStore:
    """Versionierter Abschlussnachweis fuer Quellen ohne dauerhafte Claims."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._reviews: dict[str, SourceReview] = {}
        if path.exists():
            for row in json.loads(path.read_text(encoding="utf-8")):
                review = SourceReview.model_validate(row)
                self._reviews[review.doc_id] = review

    def add(self, reviews: list[SourceReview]) -> tuple[int, int]:
        """Rueckgabe: (neu, ersetzt)."""
        new = replaced = 0
        for review in reviews:
            if review.doc_id in self._reviews:
                replaced += 1
            else:
                new += 1
            self._reviews[review.doc_id] = review
        return new, replaced

    def remove(self, doc_ids: set[str]) -> int:
        removed = 0
        for doc_id in doc_ids:
            if self._reviews.pop(doc_id, None) is not None:
                removed += 1
        return removed

    def all(self) -> list[SourceReview]:
        return sorted(self._reviews.values(), key=lambda review: review.doc_id)

    def sources(self) -> set[str]:
        return set(self._reviews)

    def current_sources(self, fingerprints: Mapping[str, str]) -> set[str]:
        """Nur Quellen, deren gepruefte Revision noch dem Corpus entspricht.

        Zurueck kommen doc_ids aus `fingerprints`, also die Sicht des Corpus,
        nicht die des Stores. Der Unterschied zaehlt, sobald eine Quelle
        umdatiert wurde: der Store kennt sie dann noch unter dem alten Pfad.

        Gematcht wird deshalb zuerst exakt und danach ueber die datumsfreie
        Signatur (Quelle, Slug, Kurz-ID). Claims loesen seit resolve_doc_id()
        genauso auf; Reviews haben das nachgezogen, weil sonst jede
        mtime-Aenderung an memory/ die halbe Warteschlange neu oeffnet.
        """
        von_signatur: dict[tuple[str, str, str], SourceReview] = {}
        for bekannt in self._reviews.values():
            signatur = doc_signature(bekannt.doc_id)
            if signatur is not None:
                von_signatur.setdefault(signatur, bekannt)

        aktuell: set[str] = set()
        for doc_id, fingerprint in fingerprints.items():
            treffer = self._reviews.get(doc_id)
            if treffer is None:
                signatur = doc_signature(doc_id)
                treffer = von_signatur.get(signatur) if signatur else None
            if treffer and treffer.source_fingerprint == fingerprint:
                aktuell.add(doc_id)
        return aktuell

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        rows = [json.loads(review.model_dump_json()) for review in self.all()]
        self.path.write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding="utf-8")
