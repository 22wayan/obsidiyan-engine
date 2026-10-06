from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest
from conftest import make_doc

from obsidiyan.corpusio import write_doc
from obsidiyan.models import Role, Sensitivity, Source, Turn
from obsidiyan.search import _parse_query, search

TODAY = date(2026, 8, 8)


def test_finds_nothing_in_empty_corpus(tmp_path: Path) -> None:
    assert search("egal", tmp_path) == []


def test_user_turn_outranks_assistant_turn(tmp_path: Path) -> None:
    """BRAIN.md-Vorrangregel: Assistant-Antworten sind keine autoritative Personenquelle."""
    write_doc(
        make_doc(
            conv_id="user-said",
            title="user",
            turns=(Turn(role=Role.USER, text="Zielgruppe ist der Mittelstand"),),
        ),
        tmp_path,
    )
    write_doc(
        make_doc(
            conv_id="assistant-said",
            title="assistant",
            turns=(Turn(role=Role.ASSISTANT, text="Zielgruppe ist der Mittelstand"),),
        ),
        tmp_path,
    )
    hits = search("Zielgruppe", tmp_path, today=TODAY)
    assert [h.doc.conv_id for h in hits] == ["user-said", "assistant-said"]


def test_recent_doc_outranks_old_doc(tmp_path: Path) -> None:
    turns = (Turn(role=Role.USER, text="Entscheidung zur Vertragsform"),)
    write_doc(make_doc(conv_id="alt", day="2025-08-01", turns=turns), tmp_path)
    write_doc(make_doc(conv_id="neu", day="2026-08-01", turns=turns), tmp_path)
    hits = search("Vertragsform", tmp_path, today=TODAY)
    assert [h.doc.conv_id for h in hits] == ["neu", "alt"]


def test_nda_is_excluded_by_default(tmp_path: Path) -> None:
    write_doc(make_doc(conv_id="geheim", sensitivity=Sensitivity.NDA), tmp_path)
    write_doc(make_doc(conv_id="offen", sensitivity=Sensitivity.CLEAN), tmp_path)
    assert [h.doc.conv_id for h in search("Entscheidung", tmp_path, today=TODAY)] == ["offen"]


def test_since_filter(tmp_path: Path) -> None:
    turns = (Turn(role=Role.USER, text="Meilenstein"),)
    write_doc(make_doc(conv_id="alt", day="2025-01-01", turns=turns), tmp_path)
    write_doc(make_doc(conv_id="neu", day="2026-07-01", turns=turns), tmp_path)
    hits = search("Meilenstein", tmp_path, since=date(2026, 1, 1), today=TODAY)
    assert [h.doc.conv_id for h in hits] == ["neu"]


def test_source_and_project_filter(tmp_path: Path) -> None:
    turns = (Turn(role=Role.USER, text="Konnektor"),)
    write_doc(
        make_doc(conv_id="cc", source=Source.CLAUDE_CODE, project="alpha", turns=turns), tmp_path
    )
    write_doc(make_doc(conv_id="cx", source=Source.CODEX, project="beta", turns=turns), tmp_path)
    assert [h.doc.conv_id for h in search("Konnektor", tmp_path, source="codex", today=TODAY)] == [
        "cx"
    ]
    assert [h.doc.conv_id for h in search("Konnektor", tmp_path, project="alph", today=TODAY)] == [
        "cc"
    ]


def test_snippet_is_returned(tmp_path: Path) -> None:
    write_doc(
        make_doc(
            conv_id="s",
            turns=(Turn(role=Role.USER, text="der Festpreis fuer die Discovery steht"),),
        ),
        tmp_path,
    )
    hits = search("Festpreis", tmp_path, today=TODAY)
    assert "Festpreis" in hits[0].snippet


def test_multi_term_query_matches_terms_in_any_order(tmp_path: Path) -> None:
    """Kern-Regression zum Eval-Befund 2026-08-09: Mehrwort-Queries sind
    UND-verknuepfte Begriffe, keine exakte Phrase."""
    write_doc(
        make_doc(
            conv_id="satzung",
            turns=(Turn(role=Role.USER, text="Der Vorstand hat die neue Satzung beschlossen"),),
        ),
        tmp_path,
    )
    hits = search("Satzung Vorstand", tmp_path, today=TODAY)
    assert [h.doc.conv_id for h in hits] == ["satzung"]


def test_full_matches_rank_before_partial_ones(tmp_path: Path) -> None:
    """Dokumente mit allen Termen stehen vorne; Teil-Treffer folgen und sind markiert."""
    write_doc(
        make_doc(
            conv_id="beide",
            turns=(Turn(role=Role.USER, text="Preismodell und Angebot fuer Kunden"),),
        ),
        tmp_path,
    )
    write_doc(
        make_doc(
            conv_id="nur-eins",
            turns=(Turn(role=Role.USER, text="Das Angebot steht"),),
        ),
        tmp_path,
    )
    hits = search("Angebot Preismodell", tmp_path, today=TODAY)
    assert [h.doc.conv_id for h in hits] == ["beide", "nur-eins"]
    assert [(h.matched_terms, h.total_terms) for h in hits] == [(2, 2), (1, 2)]
    strict = search("Angebot Preismodell", tmp_path, today=TODAY, ranking="substring")
    assert [h.doc.conv_id for h in strict] == ["beide"]


def test_quoted_phrase_requires_exact_sequence(tmp_path: Path) -> None:
    write_doc(
        make_doc(
            conv_id="phrase",
            turns=(Turn(role=Role.USER, text="Speech Recognition bleibt deaktiviert"),),
        ),
        tmp_path,
    )
    write_doc(
        make_doc(
            conv_id="getrennt",
            turns=(Turn(role=Role.USER, text="Recognition ist neu, Speech auch"),),
        ),
        tmp_path,
    )
    hits = search('"Speech Recognition"', tmp_path, today=TODAY)
    assert [h.doc.conv_id for h in hits] == ["phrase"]


def test_regex_metacharacters_are_searched_literally(tmp_path: Path) -> None:
    """Klammern und andere Metazeichen duerfen weder crashen noch als Regex wirken."""
    write_doc(
        make_doc(
            conv_id="preis",
            turns=(Turn(role=Role.USER, text="Discovery kostet 450 Euro (ungefaehr)"),),
        ),
        tmp_path,
    )
    hits = search("(ungefaehr)", tmp_path, today=TODAY)
    assert [h.doc.conv_id for h in hits] == ["preis"]


def test_blank_query_returns_nothing(tmp_path: Path) -> None:
    write_doc(make_doc(conv_id="x"), tmp_path)
    assert search("   ", tmp_path, today=TODAY) == []


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("polyglot ios speech", ["polyglot", "ios", "speech"]),
        ('"speech recognition" polyglot', ["speech recognition", "polyglot"]),
        ('"nur phrase"', ["nur phrase"]),
        ('unbalanced "quote', ["unbalanced", "quote"]),
        ("", []),
        ('""', []),
        ("   ", []),
    ],
)
def test_parse_query(query: str, expected: list[str]) -> None:
    assert _parse_query(query) == expected


def test_fallback_matches_ripgrep_simple_case_folding(tmp_path: Path) -> None:
    """ripgrep --ignore-case nutzt Unicode simple case folding (ß bleibt ß).
    Der Python-Fallback muss dieselben Kandidaten liefern, sonst haengen die
    Suchergebnisse davon ab, ob auf der Maschine ein rg-Binary liegt."""
    write_doc(
        make_doc(
            conv_id="strasse",
            turns=(Turn(role=Role.USER, text="IM STRASSENVERKEHR gilt Tempo 30"),),
        ),
        tmp_path,
    )
    # Nur ein Teil-Treffer ueber "Tempo": "straße" darf nicht als "strasse" zaehlen.
    partial = search("straße Tempo", tmp_path, today=TODAY)
    assert all(h.matched_terms < h.total_terms for h in partial)
    hits = search("strasse Tempo", tmp_path, today=TODAY)
    assert [h.doc.conv_id for h in hits] == ["strasse"]


def _install_fake_ripgrep(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Emuliert rg --files-with-matches: Pattern MUSS per -e kommen (sonst wuerde
    ein Term mit fuehrendem Bindestrich als Flag gelesen), --fixed-strings MUSS
    gesetzt sein, Matching ist simple case folding wie bei echtem rg."""
    fake_rg = tmp_path / "fake-rg"
    fake_rg.write_text(
        f"""#!{sys.executable}
import sys
from pathlib import Path

args = sys.argv[1:]
if "--fixed-strings" not in args or "-e" not in args:
    sys.exit(2)
pattern = args[args.index("-e") + 1].lower()
matched = False
for path in Path(".").rglob("*.md"):
    if pattern in path.read_text(encoding="utf-8", errors="replace").lower():
        print(path.as_posix())
        matched = True
sys.exit(0 if matched else 1)
""",
        encoding="utf-8",
    )
    fake_rg.chmod(0o755)
    monkeypatch.setattr("obsidiyan.search._ripgrep_binary", lambda: str(fake_rg))


def test_ripgrep_path_intersects_terms(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Der rg-Pfad muss pro Term literal suchen (--fixed-strings) und die
    Treffermengen schneiden. Fake-Binary, weil rg hier nur eine Shell-Funktion ist."""
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    write_doc(
        make_doc(
            conv_id="beide",
            turns=(Turn(role=Role.USER, text="Satzung und Vorstand in einem Dokument"),),
        ),
        corpus,
    )
    write_doc(
        make_doc(
            conv_id="nur-eins",
            turns=(Turn(role=Role.USER, text="nur die Satzung"),),
        ),
        corpus,
    )
    _install_fake_ripgrep(tmp_path, monkeypatch)

    hits = search("Vorstand Satzung", corpus, today=TODAY, ranking="substring")
    assert [h.doc.conv_id for h in hits] == ["beide"]


def test_leading_dash_term_is_literal_on_ripgrep_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ein Term wie --include-nda darf rg nicht als Flag erreichen."""
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    write_doc(
        make_doc(
            conv_id="flag-doc",
            turns=(Turn(role=Role.USER, text="die Suche kennt --include-nda als Option"),),
        ),
        corpus,
    )
    _install_fake_ripgrep(tmp_path, monkeypatch)

    hits = search("--include-nda", corpus, today=TODAY)
    assert [h.doc.conv_id for h in hits] == ["flag-doc"]


def test_title_counts_as_searchable_text(tmp_path: Path) -> None:
    """Session titles often name the topic the turns never repeat."""
    write_doc(
        make_doc(
            conv_id="t",
            title="Public API rate limit",
            turns=(Turn(role=Role.USER, text="We allow 60 requests per minute per key."),),
        ),
        tmp_path,
    )
    hits = search("rate limit", tmp_path, today=TODAY)
    assert [h.doc.conv_id for h in hits] == ["t"]
    assert hits[0].snippet


def test_falls_back_to_partial_matches_when_no_doc_has_every_term(tmp_path: Path) -> None:
    """Agents type natural phrases; one extra word must not wipe out every result."""
    write_doc(
        make_doc(
            conv_id="money",
            turns=(Turn(role=Role.USER, text="Store all money as integer cents."),),
        ),
        tmp_path,
    )
    write_doc(
        make_doc(conv_id="other", turns=(Turn(role=Role.USER, text="Deploy to Fly.io."),)),
        tmp_path,
    )
    hits = search("store money amounts", tmp_path, today=TODAY)
    assert [h.doc.conv_id for h in hits] == ["money"]
    assert (hits[0].matched_terms, hits[0].total_terms) == (2, 3)


def test_full_matches_report_every_term(tmp_path: Path) -> None:
    write_doc(
        make_doc(conv_id="m", turns=(Turn(role=Role.USER, text="money in cents"),)),
        tmp_path,
    )
    hits = search("money cents", tmp_path, today=TODAY)
    assert (hits[0].matched_terms, hits[0].total_terms) == (2, 2)


def test_partial_fallback_ignores_full_matches_that_are_hidden(tmp_path: Path) -> None:
    """A full match only in an NDA doc must not leave the agent with zero visible hits."""
    write_doc(
        make_doc(
            conv_id="geheim",
            sensitivity=Sensitivity.NDA,
            turns=(Turn(role=Role.USER, text="Switch payments from Stripe to Mollie."),),
        ),
        tmp_path,
    )
    write_doc(
        make_doc(
            conv_id="offen",
            sensitivity=Sensitivity.CLEAN,
            turns=(Turn(role=Role.USER, text="Stripe handles card payments."),),
        ),
        tmp_path,
    )
    hits = search("Stripe Mollie", tmp_path, today=TODAY)
    assert [h.doc.conv_id for h in hits] == ["offen"]
    assert (hits[0].matched_terms, hits[0].total_terms) == (1, 2)
    full = search("Stripe Mollie", tmp_path, include_nda=True, today=TODAY)
    assert full[0].doc.conv_id == "geheim"


def test_ripgrep_found_outside_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """MCP-Clients starten den Server mit schmalem PATH; rg muss trotzdem gefunden werden."""
    import obsidiyan.search as search_module

    fake = tmp_path / "rg"
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)
    monkeypatch.setattr(search_module.shutil, "which", lambda _name: None)
    monkeypatch.setattr(search_module, "_RG_LOCATIONS", (str(tmp_path / "fehlt"), str(fake)))
    assert search_module._ripgrep_binary() == str(fake)
    monkeypatch.setattr(search_module, "_RG_LOCATIONS", ())
    assert search_module._ripgrep_binary() is None


def test_ripgrep_narrows_later_terms_to_earlier_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ab dem zweiten Term bekommt rg nur noch die Kandidaten, nicht den ganzen Ordner."""
    import obsidiyan.search as search_module

    corpus = tmp_path / "corpus"
    corpus.mkdir()
    for i, text in enumerate(["Mollie und Stripe", "nur Stripe", "nur Mollie"]):
        write_doc(make_doc(conv_id=f"d{i}", turns=(Turn(role=Role.USER, text=text),)), corpus)
    calls: list[list[str]] = []
    real_run = search_module.subprocess.run

    def spy(cmd: list[str], **kwargs: object) -> object:
        calls.append(cmd)
        return real_run(cmd, **kwargs)  # type: ignore[call-overload]

    if search_module._ripgrep_binary() is None:
        pytest.skip("ripgrep nicht installiert")
    monkeypatch.setattr(search_module.subprocess, "run", spy)
    hits = search("Stripe Mollie", corpus, today=TODAY, ranking="substring")
    assert [h.doc.conv_id for h in hits] == ["d0"]
    assert calls[0][-1] == "."
    assert "--" in calls[1] and "." not in calls[1][calls[1].index("--") :]
