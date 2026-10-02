"""Datenmodell der Destillation.

Eine destillierte Note besteht nicht aus Fliesstext, sondern aus Claims. Jeder
Claim traegt seine Quelle. Das ist die einzige Verteidigung gegen halluziniertes
Wissen: was keine Quelle hat, kommt nicht in die Note.

Der `key` ist das Feld, an dem die Konfliktaufloesung haengt. Zwei Claims mit
demselben key sind Aussagen ueber DENSELBEN Sachverhalt. Der juengere gewinnt,
der aeltere wird als ueberholt markiert statt geloescht. Das bildet die
Vorrangregel aus BRAIN.md mechanisch ab.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ClaimKind(StrEnum):
    FACT = "fakt"
    DECISION = "entscheidung"
    PREFERENCE = "praeferenz"
    OPEN = "offen"


class Confidence(StrEnum):
    """Beweisklasse der Quelle, nicht Sicherheit des Modells.

    HIGH   aus einem woertlichen Nutzer-Turn
    MEDIUM aus einer Harness-Zusammenfassung: inhaltlich echt, nicht woertlich
    """

    HIGH = "high"
    MEDIUM = "medium"


class ReviewReason(StrEnum):
    """Ergebnis einer vollstaendigen Quellenpruefung."""

    CLAIMS_EXTRACTED = "claims_extracted"
    COURSEWORK = "coursework"
    TRANSIENT = "transient"
    THIRD_PARTY = "third_party"
    REPO_STATE = "repo_state"
    DUPLICATE = "duplicate"
    PERSONAL_EPHEMERA = "personal_ephemera"
    SENSITIVE = "sensitive"
    OTHER = "other"


class Claim(BaseModel):
    """Eine belegte Einzelaussage aus einem Nutzer-Turn."""

    model_config = ConfigDict(frozen=True)

    entity: str = Field(
        default="",
        description="Uebergeordneter Knoten (Projekt, Person, Firma). Leer = freistehend",
    )
    topic: str = Field(description="Titel der Note, in die der Claim gehoert")
    key: str = Field(description="Normalisierter Sachverhalt. Gleicher key = derselbe Gegenstand")
    kind: ClaimKind
    text: str = Field(description="Die Aussage, ein Satz, ohne Fuellwoerter")
    stated_on: date | None = None
    source_doc_id: str = Field(description="Pfad relativ zu corpus/")
    # Default bewusst konservativ: eine Aussage ohne ausgewiesene Beweisklasse
    # wird lieber zu schwach als zu stark behauptet.
    confidence: Confidence = Confidence.MEDIUM
    rationale: str = Field(default="", description="Begruendung, falls im Original genannt")

    @property
    def is_dated(self) -> bool:
        return self.stated_on is not None


class ClaimSet(BaseModel):
    """Extraktionsergebnis eines Durchlaufs. Wird als JSON zwischengespeichert."""

    claims: tuple[Claim, ...] = ()

    def by_topic(self) -> dict[str, list[Claim]]:
        out: dict[str, list[Claim]] = {}
        for claim in self.claims:
            out.setdefault(claim.topic, []).append(claim)
        return out


class SourceReview(BaseModel):
    """Beleg, welche Revision einer Kandidatenquelle geprueft wurde."""

    model_config = ConfigDict(frozen=True)

    doc_id: str = Field(description="Pfad relativ zu corpus/")
    source_fingerprint: str = Field(
        default="",
        pattern=r"^(?:[0-9a-f]{16})?$",
        description="Revision des autoritativen Nutzerinhalts; die CLI setzt sie",
    )
    reason: ReviewReason
    reviewed_on: date
    note: str = Field(
        default="",
        max_length=240,
        description="Kurze generische Begruendung, ohne Rohtext oder sensible Details",
    )
