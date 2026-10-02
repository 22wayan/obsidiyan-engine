from __future__ import annotations

from obsidiyan.search import _window


def test_window_does_not_cut_words() -> None:
    text = "The whole team only knows SQL, and the hosting provider offers managed Postgres."
    start = text.index("Postgres")
    snippet = _window(text, start, start + len("Postgres"), before=20, after=5)
    assert snippet == "provider offers managed Postgres."


def test_window_keeps_short_text_whole() -> None:
    assert _window("use Postgres", 4, 12) == "use Postgres"
