"""Die Layer-Grenze beim Destillieren.

NDA wird destilliert, aber das Destillat darf nie in den git-getrackten Layer.
Die Stufe kommt aus dem Corpus, nicht aus dem Claim, damit ein Vertipper des
extrahierenden Agenten die Grenze nicht oeffnen kann.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from conftest import make_doc

from obsidiyan.corpusio import write_doc
from obsidiyan.distill.models import Claim, ClaimKind
from obsidiyan.distill.route import RoutingError, route
from obsidiyan.models import Sensitivity


def _claim(topic: str, doc_id: str, key: str = "k") -> Claim:
    return Claim(
        entity="Projekt",
        topic=topic,
        key=key,
        kind=ClaimKind.FACT,
        text="Eine belegte Aussage.",
        stated_on=date(2026, 8, 24),
        source_doc_id=doc_id,
    )


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    write_doc(make_doc(conv_id="offen", sensitivity=Sensitivity.CLEAN), tmp_path)
    write_doc(make_doc(conv_id="geheim", sensitivity=Sensitivity.NDA), tmp_path)
    write_doc(make_doc(conv_id="kurs", sensitivity=Sensitivity.COPYRIGHT), tmp_path)
    return tmp_path


def _doc_id(corpus: Path, marker: str) -> str:
    return next(
        p.relative_to(corpus).as_posix() for p in corpus.rglob("*.md") if marker in p.read_text()
    )


def test_clean_source_goes_to_notes(corpus: Path) -> None:
    routed = route([_claim("Offenes Thema", _doc_id(corpus, "offen"))], corpus)

    assert len(routed.clean) == 1
    assert routed.private == []


def test_nda_source_goes_to_private(corpus: Path) -> None:
    routed = route([_claim("Vertrauliches Thema", _doc_id(corpus, "geheim"))], corpus)

    assert routed.clean == []
    assert len(routed.private) == 1


def test_copyright_source_is_refused(corpus: Path) -> None:
    with pytest.raises(RoutingError, match="urheberrechtlich"):
        route([_claim("Kursthema", _doc_id(corpus, "kurs"))], corpus)


def test_a_topic_may_not_mix_levels(corpus: Path) -> None:
    """Eine Note kann nicht halb privat sein."""
    claims = [
        _claim("Gemischt", _doc_id(corpus, "offen"), key="a"),
        _claim("Gemischt", _doc_id(corpus, "geheim"), key="b"),
    ]

    with pytest.raises(RoutingError, match="mischt"):
        route(claims, corpus)


def test_missing_source_is_refused_instead_of_guessed(corpus: Path) -> None:
    with pytest.raises(RoutingError, match="fehlt"):
        route([_claim("Thema", "gibt/es/nicht.md")], corpus)


def test_the_corpus_decides_not_the_claim(corpus: Path) -> None:
    """Der Claim traegt keine eigene Stufe, die Quelle bestimmt sie."""
    claim = _claim("Vertrauliches Thema", _doc_id(corpus, "geheim"))

    assert "sensitivity" not in claim.model_dump()
    assert route([claim], corpus).private == [claim]


def test_emit_writes_the_nda_distillate_into_private_not_notes(tmp_path: Path) -> None:
    """Der Durchstich: eine NDA-Quelle wird zu einer Note in private/."""
    from obsidiyan.distill.emit import emit

    corpus = tmp_path / "corpus"
    write_doc(make_doc(conv_id="geheim", sensitivity=Sensitivity.NDA), corpus)
    doc_id = next(p.relative_to(corpus).as_posix() for p in corpus.rglob("*.md"))

    notes = tmp_path / "notes"
    notes.mkdir()
    private = tmp_path / "private"
    private.mkdir()
    (private / "INDEX.md").write_text("# Privater Index\n", encoding="utf-8")

    routed = route([_claim("Mandat Stand", doc_id)], corpus)
    assert routed.clean == []

    emit(routed.private, private, entity_parents={"Projekt": "private/INDEX"}, private=True)

    written = sorted(p.relative_to(tmp_path).as_posix() for p in private.rglob("*.md"))
    assert "private/mandat-stand.md" in written
    assert list(notes.rglob("*.md")) == []


def test_the_tracked_claim_store_may_not_reference_an_nda_source(tmp_path: Path) -> None:
    """Das Leck, das die NDA-Destillation aufgerissen haette.

    scripts/auto-distill.sh macht `git add distill` und oeffnet einen PR. Ein
    Claim aus einer NDA-Quelle in der getrackten claims.json waere damit der
    direkte Weg, Kundenwissen nach GitHub zu schieben.
    """
    import json

    from obsidiyan.provenance import distill_provenance_errors

    corpus = tmp_path / "corpus"
    nda = write_doc(make_doc(conv_id="geheim", sensitivity=Sensitivity.NDA), corpus)
    doc_id = nda.relative_to(corpus).as_posix()

    tracked = tmp_path / "claims.json"
    tracked.write_text(json.dumps([{"source_doc_id": doc_id}]), encoding="utf-8")

    strict = distill_provenance_errors(corpus, {tracked: "source_doc_id"}, require_clean=True)
    lax = distill_provenance_errors(corpus, {tracked: "source_doc_id"})

    assert any("gehoert in den privaten Store" in error for error in strict)
    assert lax == []


def _one_claim(entity: str, topic: str) -> Claim:
    return Claim(
        entity=entity,
        topic=topic,
        key="k",
        kind=ClaimKind.FACT,
        text="Eine belegte Aussage.",
        stated_on=date(2026, 8, 24),
        source_doc_id="egal.md",
    )


def test_notes_layer_stays_flat(tmp_path: Path) -> None:
    """Regression: in notes/ liegen alle Parents in der Wurzel, nichts verschiebt sich."""
    from obsidiyan.distill.emit import emit

    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "brain.md").write_text("# BRAIN\n", encoding="utf-8")

    emit([_one_claim("Projekt", "Projekt Thema")], notes, entity_parents={"Projekt": "brain"})

    assert sorted(p.name for p in notes.rglob("*.md")) == [
        "brain.md",
        "projekt-thema.md",
        "projekt.md",
    ]
    assert all(p.parent == notes for p in notes.rglob("*.md"))


def test_private_note_lands_beside_its_parent(tmp_path: Path) -> None:
    """Der Mandatsordner gewinnt: emit folgt remember_private statt daneben zu schreiben."""
    from obsidiyan.distill.emit import emit

    private = tmp_path / "private"
    (private / "acme").mkdir(parents=True)
    (private / "INDEX.md").write_text("# Index\n", encoding="utf-8")
    (private / "acme" / "mandat-private.md").write_text(
        '---\nparent: "[[private/INDEX]]"\n---\n\n# Mandat\n', encoding="utf-8"
    )

    emit(
        [_one_claim("Docparse", "Docparse Thema")],
        private,
        entity_parents={"Docparse": "private/acme/mandat-private"},
        private=True,
    )

    assert (private / "acme" / "docparse.md").is_file()
    assert (private / "acme" / "docparse-thema.md").is_file()
    assert not (private / "docparse.md").exists()


def test_an_existing_note_in_a_subfolder_is_continued_not_duplicated(tmp_path: Path) -> None:
    from obsidiyan.distill.emit import emit

    private = tmp_path / "private"
    (private / "acme").mkdir(parents=True)
    (private / "INDEX.md").write_text("# Index\n", encoding="utf-8")
    (private / "acme" / "docparse.md").write_text(
        '---\ncreated: 2026-08-01\nmodified: 2026-08-01\nparent: "[[private/INDEX]]"\n'
        'distilled_from: 1 Quelle\n---\n\n# Docparse\n', encoding="utf-8"
    )

    emit([_one_claim("Docparse", "Docparse Thema")], private, private=True)

    assert [p.relative_to(private).as_posix() for p in sorted(private.rglob("docparse.md"))] == [
        "acme/docparse.md"
    ]


def test_the_same_note_name_twice_in_a_layer_is_refused(tmp_path: Path) -> None:
    """Zwei gleichnamige Notes waeren eine stille Dublette, also lieber ein Fehler."""
    from obsidiyan.distill.emit import emit

    private = tmp_path / "private"
    (private / "a").mkdir(parents=True)
    (private / "b").mkdir(parents=True)
    (private / "INDEX.md").write_text("# Index\n", encoding="utf-8")
    for folder in ("a", "b"):
        (private / folder / "docparse.md").write_text("# Docparse\n", encoding="utf-8")

    with pytest.raises(ValueError, match="liegt mehrfach"):
        emit([_one_claim("Docparse", "Thema")], private, private=True)
