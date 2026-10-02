from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import write_codex_session

from obsidiyan import ingest
from obsidiyan.corpusio import iter_docs, read_doc
from obsidiyan.models import Role, Sensitivity
from obsidiyan.sources import codex


def test_codex_keeps_only_human_readable_turns(tmp_path: Path) -> None:
    doc = codex.parse_session(write_codex_session(tmp_path / "sessions"))
    assert doc is not None
    assert [t.role for t in doc.turns] == [Role.USER, Role.ASSISTANT]
    body = "\n".join(t.text for t in doc.turns)
    assert "DENKEN" not in body
    assert "codex nutzerfrage" in body
    assert "codex antwort" in body


def test_codex_does_not_duplicate_response_items(tmp_path: Path) -> None:
    """user_message und response_item/message tragen denselben Text. Nur einmal aufnehmen."""
    doc = codex.parse_session(write_codex_session(tmp_path / "sessions"))
    assert doc is not None
    assert sum(1 for t in doc.turns if "codex nutzerfrage" in t.text) == 1


def test_codex_metadata(tmp_path: Path) -> None:
    doc = codex.parse_session(write_codex_session(tmp_path / "sessions"))
    assert doc is not None
    assert doc.conv_id == "roll-1"
    assert doc.project == "demo"
    assert doc.title == "codex nutzerfrage"


def test_codex_run_writes_corpus(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    corpus = tmp_path / "corpus"
    write_codex_session(sessions)
    stats = ingest.run(
        "codex",
        corpus,
        session_root=sessions,
        require_source_overrides=False,
    )
    assert stats.written == 1
    assert len(list(iter_docs(corpus))) == 1


def test_source_override_reclassifies_unchanged_session(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    corpus = tmp_path / "corpus"
    overrides = tmp_path / "overrides.json"
    write_codex_session(sessions, rollout_id="private-session")
    overrides.write_text('{"version": 1, "sources": []}', encoding="utf-8")

    first = ingest.run(
        "codex",
        corpus,
        session_root=sessions,
        source_overrides_path=overrides,
        require_source_overrides=False,
    )
    first_doc = read_doc(next(iter(iter_docs(corpus))))
    assert first.written == 1
    assert first_doc.sensitivity is Sensitivity.CLEAN

    overrides.write_text(
        json.dumps(
            {
                "version": 1,
                "sources": [{"source": "codex", "conv_id": "private-session"}],
            }
        ),
        encoding="utf-8",
    )
    second = ingest.run(
        "codex",
        corpus,
        session_root=sessions,
        source_overrides_path=overrides,
        require_source_overrides=True,
    )
    second_doc = read_doc(next(iter(iter_docs(corpus))))
    assert second.written == 1
    assert second_doc.sensitivity is Sensitivity.NDA

    overrides.unlink()
    with pytest.raises(FileNotFoundError, match="overrides missing"):
        ingest.run(
            "codex",
            corpus,
            session_root=sessions,
            source_overrides_path=overrides,
            require_source_overrides=True,
        )
    assert read_doc(next(iter(iter_docs(corpus)))).sensitivity is Sensitivity.NDA


def test_first_session_meta_wins(tmp_path: Path) -> None:
    """Mehrere session_meta-Zeilen pro Datei: sonst kollidieren Rollouts auf einer conv_id."""
    import json

    path = write_codex_session(tmp_path / "sessions", rollout_id="erste")
    with path.open("a", encoding="utf-8") as fh:
        fh.write(
            json.dumps({"type": "session_meta", "payload": {"id": "zweite", "cwd": "/x"}}) + "\n"
        )
    doc = codex.parse_session(path)
    assert doc is not None
    assert doc.conv_id == "erste"
