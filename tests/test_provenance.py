from __future__ import annotations

import json
from pathlib import Path

from conftest import make_doc

from obsidiyan.corpusio import write_doc
from obsidiyan.models import Sensitivity
from obsidiyan.provenance import distill_provenance_errors


def _store(path: Path, field: str, doc_id: str) -> None:
    path.write_text(json.dumps([{field: doc_id}]), encoding="utf-8")


def test_clean_existing_provenance_passes(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    source = write_doc(make_doc(conv_id="clean"), corpus)
    claims = tmp_path / "claims.json"
    _store(claims, "source_doc_id", source.relative_to(corpus).as_posix())

    assert distill_provenance_errors(corpus, {claims: "source_doc_id"}) == []


def test_missing_provenance_fails(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    claims = tmp_path / "claims.json"
    _store(claims, "source_doc_id", "missing.md")

    errors = distill_provenance_errors(corpus, {claims: "source_doc_id"})

    assert any("Quelle fehlt" in error for error in errors)


def test_nda_source_is_allowed_since_its_distillate_goes_private(tmp_path: Path) -> None:
    """Seit dem 24.08.2026 ist NDA eine zulaessige Claim-Quelle.

    Das Destillat landet dann in private/ statt in notes/, die Trennung macht
    obsidiyan.distill.route beim Emit.
    """
    corpus = tmp_path / "corpus"
    nda_source = write_doc(make_doc(conv_id="nda", sensitivity=Sensitivity.NDA), corpus)
    claims = tmp_path / "claims.json"
    _store(claims, "source_doc_id", nda_source.relative_to(corpus).as_posix())

    assert distill_provenance_errors(corpus, {claims: "source_doc_id"}) == []


def test_copyright_source_stays_forbidden(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    source = write_doc(make_doc(conv_id="kurs", sensitivity=Sensitivity.COPYRIGHT), corpus)
    claims = tmp_path / "claims.json"
    _store(claims, "source_doc_id", source.relative_to(corpus).as_posix())

    errors = distill_provenance_errors(corpus, {claims: "source_doc_id"})

    assert any("urheberrechtlich geschuetzt" in error for error in errors)


def test_symlink_and_traversal_provenance_are_not_followed(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    external = tmp_path / "external.md"
    external.write_text("private", encoding="utf-8")
    (corpus / "linked.md").symlink_to(external)
    claims = tmp_path / "claims.json"
    reviews = tmp_path / "reviews.json"
    _store(claims, "source_doc_id", "linked.md")
    _store(reviews, "doc_id", "../external.md")

    errors = distill_provenance_errors(
        corpus,
        {claims: "source_doc_id", reviews: "doc_id"},
    )

    assert any("Symlink-Provenienz" in error for error in errors)
    assert any("ungueltiger Corpus-Pfad" in error for error in errors)


def test_a_moved_memory_doc_is_still_resolved(tmp_path: Path) -> None:
    """Regression: ein verschobenes Datum im Pfad darf keine Provenienz brechen.

    Memory-Snapshots ohne created/modified erben die Dateizeit. sync-memory.sh
    schreibt sie bei jedem Refresh neu, damit springt das Datum im Corpus-Pfad
    auf heute. Am 2026-08-28 verloren so 122 Claims ihre Quelle, obwohl kein
    einziges Dokument wirklich fehlte.
    """
    from obsidiyan.provenance import resolve_doc_id

    corpus = tmp_path / "corpus"
    neu = write_doc(make_doc(conv_id="wandernd", day="2026-08-28"), corpus)
    alt = neu.parent.parent / "2026" / neu.name.replace("2026-08-28", "2026-06-03")

    gefunden = resolve_doc_id(alt.relative_to(corpus).as_posix(), corpus)

    assert gefunden == neu


def test_a_claim_on_a_moved_source_is_not_reported_as_missing(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    neu = write_doc(make_doc(conv_id="wandernd", day="2026-08-28"), corpus)
    alter_pfad = neu.relative_to(corpus).as_posix().replace("2026-08-28", "2026-06-03")
    claims = tmp_path / "claims.json"
    _store(claims, "source_doc_id", alter_pfad)

    assert distill_provenance_errors(corpus, {claims: "source_doc_id"}) == []


def test_a_genuinely_missing_source_is_still_reported(tmp_path: Path) -> None:
    """Die Nachsicht gilt nur fuer verschobene Pfade, nicht fuer fehlende Quellen."""
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    claims = tmp_path / "claims.json"
    _store(claims, "source_doc_id", "2026/2026-06-03--memory--gibt-es-nicht--deadbeef.md")

    errors = distill_provenance_errors(corpus, {claims: "source_doc_id"})

    assert any("Quelle fehlt" in error for error in errors)
