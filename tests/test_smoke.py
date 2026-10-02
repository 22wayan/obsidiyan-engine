from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from conftest import make_doc

from obsidiyan.cli import build_parser, main
from obsidiyan.corpusio import write_doc
from obsidiyan.distill.models import ReviewReason, SourceReview
from obsidiyan.distill.select import select
from obsidiyan.distill.store import ReviewStore
from obsidiyan.ingest import ADAPTERS
from obsidiyan.models import Role, Sensitivity, Turn


def test_adapters_registered() -> None:
    assert set(ADAPTERS) == {
        "claude-code",
        "codex",
        "memory",
        "chatgpt",
        "gemini",
        "claude-web",
        "course",
    }


def test_parser_accepts_ingest() -> None:
    args = build_parser().parse_args(["ingest", "--source", "codex", "--dry-run"])
    assert args.source == "codex"
    assert args.dry_run is True


def test_stats_on_empty_corpus(tmp_path: Path, capsys: object) -> None:
    assert main(["--corpus", str(tmp_path), "stats"]) == 0


def test_pending_cli_reopens_reviewed_source_after_content_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source_path = write_doc(
        make_doc(
            conv_id="reviewed",
            turns=(Turn(role=Role.USER, text="Dauerhafter Nutzertext. " * 150),),
        ),
        tmp_path,
    )
    candidate = select(tmp_path)[0]
    claims_path = tmp_path / "claims.json"
    reviews_path = tmp_path / "reviews.json"
    rows = [
        SourceReview(
            doc_id=source_path.relative_to(tmp_path).as_posix(),
            source_fingerprint=candidate.source_fingerprint,
            reason=ReviewReason.COURSEWORK,
            reviewed_on=date(2026, 8, 9),
        )
    ]
    review_store = ReviewStore(reviews_path)
    review_store.add(rows)
    review_store.save()

    import obsidiyan.distill.store as store_module

    monkeypatch.setattr(store_module, "DEFAULT_PATH", claims_path)
    monkeypatch.setattr(store_module, "DEFAULT_REVIEW_PATH", reviews_path)

    assert main(["--corpus", str(tmp_path), "distill", "--pending"]) == 0
    assert "0 Kandidaten" in capsys.readouterr().out

    write_doc(
        make_doc(
            conv_id="reviewed",
            turns=(
                Turn(role=Role.USER, text="Dauerhafter Nutzertext. " * 150),
                Turn(role=Role.USER, text="Neue dauerhafte Entscheidung. " * 100),
            ),
        ),
        tmp_path,
    )

    assert main(["--corpus", str(tmp_path), "distill", "--pending"]) == 0
    assert "1 Kandidaten" in capsys.readouterr().out


def test_add_claims_accepts_an_nda_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = write_doc(
        make_doc(
            conv_id="private",
            sensitivity=Sensitivity.NDA,
            turns=(Turn(role=Role.USER, text="Vertraulicher Nutzertext. " * 150),),
        ),
        tmp_path,
    )
    claims_file = tmp_path / "incoming.json"
    claims_file.write_text(
        json.dumps(
            [
                {
                    "topic": "Test",
                    "key": "private",
                    "kind": "fakt",
                    "text": "Wird gespeichert und landet spaeter in private/",
                    "stated_on": "2026-08-09",
                    "source_doc_id": source.relative_to(tmp_path).as_posix(),
                    "confidence": "high",
                }
            ]
        ),
        encoding="utf-8",
    )

    import obsidiyan.distill.store as store_module

    monkeypatch.setattr(store_module, "DEFAULT_PATH", tmp_path / "store.json")
    monkeypatch.setattr(store_module, "DEFAULT_REVIEW_PATH", tmp_path / "reviews.json")
    monkeypatch.setattr(store_module, "DEFAULT_PRIVATE_PATH", tmp_path / "store-private.json")
    monkeypatch.setattr(
        store_module, "DEFAULT_PRIVATE_REVIEW_PATH", tmp_path / "reviews-private.json"
    )

    # Seit dem 24.08.2026 ist NDA eine zulaessige Quelle. Der Schutz sitzt nicht
    # mehr beim Annehmen des Claims, sondern beim Sortieren: der Claim landet im
    # gitignorierten Store, der getrackte bleibt leer.
    assert main(["--corpus", str(tmp_path), "add-claims", str(claims_file)]) == 0
    assert "0 clean, 1 privat" in capsys.readouterr().out
    assert json.loads((tmp_path / "store-private.json").read_text(encoding="utf-8"))
    # Der getrackte Store bleibt leer; genau er wird vom Auto-Distill-Lauf
    # committet und geht als PR nach GitHub.
    assert json.loads((tmp_path / "store.json").read_text(encoding="utf-8")) == []
