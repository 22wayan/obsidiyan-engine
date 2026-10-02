from __future__ import annotations

from obsidiyan.cli import _join_query


def test_plain_words_stay_separate_terms() -> None:
    assert _join_query(["postgres", "backups"]) == "postgres backups"


def test_an_argument_with_spaces_becomes_a_phrase_again() -> None:
    assert _join_query(["postgres", "managed backups"]) == 'postgres "managed backups"'
