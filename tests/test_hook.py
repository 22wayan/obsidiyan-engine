"""UserPromptSubmit-Hook: wann er ausloest, was er sucht, dass er nie blockiert."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from conftest import make_doc

from obsidiyan import hook
from obsidiyan.corpusio import write_doc
from obsidiyan.models import Role, Sensitivity, Turn

ENTITIES = {"ledgerly", "polyglot", "nordwind", "acme"}


@pytest.mark.parametrize(
    ("prompt", "fire"),
    [
        ("weisst du noch, wie wir das geloest haben?", True),
        ("wie ist der stand bei den Angeboten", True),
        ("baue bei ledgerly den Preisrechner um", True),
        ("fix den typo in der README", False),
        ("ja mach das", False),
        ("ok", False),
        ("<!-- obsidiyan-automation: Lauf --> fasse zusammen", False),
        ("Reply with exactly OK. Do not use tools.", False),
    ],
)
def test_decide(prompt: str, fire: bool) -> None:
    assert hook.decide(prompt, ENTITIES).fire is fire


def test_current_repo_name_does_not_trigger() -> None:
    assert not hook.decide("refactor the polyglot scheduler", ENTITIES, "polyglot-app").fire
    assert hook.decide("refactor the polyglot scheduler", ENTITIES, "other-repo").fire


def test_conductor_block_is_ignored() -> None:
    block = "<system_instruction>You work inside Conductor. nordwind nordwind</system_instruction>"
    prompt = block + "\nfix tests"
    assert not hook.decide(prompt, ENTITIES).fire


def test_entity_is_found_even_when_a_cue_fired() -> None:
    decision = hook.decide("weisst du noch, wie bei ledgerly die Preise waren?", ENTITIES)
    assert (decision.fire, decision.entity) == (True, "ledgerly")


def test_query_drops_cues_and_requires_the_entity() -> None:
    prompt = "weisst du noch wie wir bei ledgerly die Preise festgelegt haben"
    query = hook.build_query(prompt, "ledgerly")
    assert query.startswith('"ledgerly"')
    assert "weisst du noch" not in query.lower()


def test_entities_come_from_note_file_names(tmp_path: Path) -> None:
    (tmp_path / "notes").mkdir()
    names = ("ledgerly-preis-und-angebotsmodell.md", "karriere-und-ausbildung.md", "nordwind.md")
    for name in names:
        (tmp_path / "notes" / name).write_text("x")
    assert hook.known_entities(tmp_path) == {"ledgerly", "nordwind"}


def _vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    (vault / "notes").mkdir(parents=True)
    (vault / "notes" / "ledgerly.md").write_text("x")
    corpus = vault / "corpus"
    turn = Turn(role=Role.USER, text="Bei ledgerly kostet die Landingpage 490 Euro.")
    write_doc(make_doc(conv_id="preise", turns=(turn,)), corpus)
    secret = Turn(role=Role.USER, text="ledgerly Kunde zahlt Sonderpreis")
    write_doc(make_doc(conv_id="geheim", turns=(secret,), sensitivity=Sensitivity.NDA), corpus)
    return vault


def test_main_injects_hits_and_only_counts_confidential_ones(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    vault = _vault(tmp_path)
    monkeypatch.setenv("OBSIDIYAN_HOME", str(vault))
    payload = {"prompt": "was kostet bei ledgerly die Landingpage?", "cwd": str(tmp_path)}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    assert hook.main() == 0
    context = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert "490 Euro" in context
    assert "Sonderpreis" not in context
    assert "1 weitere Treffer sind vertraulich" in context


@pytest.mark.parametrize("stdin", ["", "kein json", '{"prompt": 5}'])
def test_main_never_blocks(
    stdin: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    assert hook.main() == 0
    assert capsys.readouterr().out == ""

