from __future__ import annotations

from datetime import date
from pathlib import Path

from obsidiyan.distill.models import (
    Claim,
    ClaimKind,
    Confidence,
    ReviewReason,
    SourceReview,
)
from obsidiyan.distill.store import ClaimStore, ReviewStore


def _c(key: str, text: str, src: str = "a.md", topic: str = "T") -> Claim:
    return Claim(
        topic=topic,
        key=key,
        kind=ClaimKind.FACT,
        text=text,
        stated_on=date(2026, 6, 29),
        source_doc_id=src,
        confidence=Confidence.HIGH,
    )


def test_accumulates_across_runs(tmp_path: Path) -> None:
    """Der Kern: Claims sammeln sich ueber Laeufe an, statt ueberschrieben zu werden."""
    p = tmp_path / "claims.json"
    first = ClaimStore(p)
    first.add([_c("k1", "A", src="doc1.md")])
    first.save()

    second = ClaimStore(p)
    new, replaced = second.add([_c("k2", "B", src="doc2.md")])
    second.save()
    assert (new, replaced) == (1, 0)
    assert len(ClaimStore(p).all()) == 2


def test_same_key_from_different_sources_both_survive(tmp_path: Path) -> None:
    """Zwei Quellen zum selben Sachverhalt sind der Rohstoff der Konfliktaufloesung."""
    store = ClaimStore(tmp_path / "c.json")
    store.add([_c("preis", "kostenlos", src="alt.md"), _c("preis", "Festpreis", src="neu.md")])
    assert len(store.all()) == 2


def test_reextraction_of_same_source_replaces_instead_of_duplicating(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "c.json")
    store.add([_c("preis", "grobe Fassung", src="doc.md")])
    new, replaced = store.add([_c("preis", "praezisere Fassung", src="doc.md")])
    assert (new, replaced) == (0, 1)
    assert [c.text for c in store.all()] == ["praezisere Fassung"]


def test_roundtrip_preserves_fields(tmp_path: Path) -> None:
    p = tmp_path / "c.json"
    store = ClaimStore(p)
    store.add([_c("k", "Text")])
    store.save()
    back = ClaimStore(p).all()[0]
    assert back.confidence is Confidence.HIGH
    assert back.stated_on == date(2026, 6, 29)


def test_topics_and_sources_report(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "c.json")
    store.add(
        [_c("a", "x", topic="Teamco"), _c("b", "y", topic="Teamco"), _c("c", "z", topic="neben")]
    )
    assert store.topics() == {"Teamco": 2, "neben": 1}
    assert store.sources() == {"a.md"}


def test_default_path_is_versioned_not_in_gitignored_corpus() -> None:
    """Claims sind teuer und nicht reproduzierbar, corpus/ ist billig und gitignored."""
    from obsidiyan.distill.store import DEFAULT_PATH

    assert DEFAULT_PATH.parent.name == "distill"
    assert "corpus" not in DEFAULT_PATH.parts


def test_same_subject_on_different_days_from_one_source_both_survive(tmp_path: Path) -> None:
    """Ein Dokument kann sich ueber Tage ziehen und denselben Punkt neu festlegen."""
    store = ClaimStore(tmp_path / "c.json")
    frueh = _c("addon", "190 Euro", src="doc.md")
    spaet = Claim(
        topic="T", key="addon", kind=ClaimKind.DECISION, text="149 Euro",
        stated_on=date(2026, 7, 26), source_doc_id="doc.md", confidence=Confidence.MEDIUM,
    )
    new, replaced = store.add([frueh, spaet])
    assert (new, replaced) == (2, 0)
    assert {c.text for c in store.all()} == {"190 Euro", "149 Euro"}


def test_review_store_roundtrip_replace_and_remove(tmp_path: Path) -> None:
    path = tmp_path / "reviews.json"
    store = ReviewStore(path)
    first = SourceReview(
        doc_id="chatgpt/2025/example.md",
        source_fingerprint="0123456789abcdef",
        reason=ReviewReason.COURSEWORK,
        reviewed_on=date(2026, 8, 9),
        note="Kursaufgabe ohne dauerhafte persoenliche Entscheidung",
    )
    assert store.add([first]) == (1, 0)
    store.save()

    loaded = ReviewStore(path)
    replacement = first.model_copy(update={"reason": ReviewReason.DUPLICATE})
    assert loaded.add([replacement]) == (0, 1)
    assert loaded.sources() == {"chatgpt/2025/example.md"}
    assert loaded.current_sources(
        {"chatgpt/2025/example.md": "0123456789abcdef"}
    ) == {"chatgpt/2025/example.md"}
    assert loaded.current_sources(
        {"chatgpt/2025/example.md": "fedcba9876543210"}
    ) == set()
    assert loaded.all()[0].reason is ReviewReason.DUPLICATE
    assert loaded.remove({"chatgpt/2025/example.md"}) == 1
    assert loaded.all() == []


def test_claim_store_removes_every_claim_from_invalid_sources(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "claims.json")
    store.add(
        [
            _c("a", "A", src="clean.md"),
            _c("b", "B", src="private.md"),
            _c("c", "C", src="private.md"),
        ]
    )

    assert store.remove_sources({"private.md"}) == 2
    assert store.sources() == {"clean.md"}
