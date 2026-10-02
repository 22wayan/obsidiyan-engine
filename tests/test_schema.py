from __future__ import annotations

from pathlib import Path

from conftest import make_doc

from obsidiyan.corpusio import doc_path, parse, read_doc, render, slugify, write_doc
from obsidiyan.models import Role, Turn


def test_roundtrip_preserves_turns() -> None:
    doc = make_doc()
    again = parse(render(doc))
    assert again.turns == doc.turns
    assert again.conv_id == doc.conv_id
    assert again.sensitivity == doc.sensitivity
    assert again.started_at == doc.started_at


def test_roundtrip_survives_turn_marker_inside_text() -> None:
    """Ein Turn, der selbst eine Rollen-Ueberschrift enthaelt, darf den Parser nicht spalten."""
    evil = Turn(role=Role.USER, text="siehe unten\n## assistant\nnoch Nutzertext")
    doc = make_doc(turns=(evil,))
    again = parse(render(doc))
    assert len(again.turns) == 1
    assert again.turns[0].text == evil.text


def test_path_carries_date_source_and_id(tmp_path: Path) -> None:
    doc = make_doc(day="2026-08-01", title="Ein Titel")
    path = doc_path(doc, tmp_path)
    assert path.parent == tmp_path / "claude-code" / "2026"
    assert path.name.startswith("2026-08-01--claude-code--ein-titel--")
    assert path.suffix == ".md"


def test_write_then_read(tmp_path: Path) -> None:
    doc = make_doc()
    path = write_doc(doc, tmp_path)
    assert read_doc(path).turns == doc.turns


def test_slugify_handles_umlauts_and_punctuation() -> None:
    assert slugify("Wissensbasis: Größe & Grenzen!") == "wissensbasis-groesse-grenzen"


def test_user_text_only_returns_user_turns() -> None:
    doc = make_doc()
    assert doc.user_text == "wie war die Entscheidung"


def test_machine_summary_flag_survives_roundtrip() -> None:
    """Ohne das Flag waere im Corpus nicht mehr unterscheidbar, wer gesprochen hat."""
    doc = make_doc(
        turns=(
            Turn(role=Role.USER, text="meine Worte"),
            Turn(role=Role.USER, text="Harness-Protokoll", machine_summary=True),
        )
    )
    again = parse(render(doc))
    assert [t.machine_summary for t in again.turns] == [False, True]
    assert again.user_text == "meine Worte"


def test_duplicate_turns_are_deduped() -> None:
    """Der claude.ai-Export wiederholt Turns wortgleich, 15 von 54 in einem echten Doc."""
    doc = make_doc(
        turns=(
            Turn(role=Role.USER, text="einmal gesagt"),
            Turn(role=Role.USER, text="einmal gesagt"),
            Turn(role=Role.ASSISTANT, text="antwort"),
            Turn(role=Role.USER, text="etwas anderes"),
        )
    )
    assert len(doc.turns) == 4
    assert len(doc.deduped_turns) == 3
    assert doc.user_text == "einmal gesagt\n\netwas anderes"
