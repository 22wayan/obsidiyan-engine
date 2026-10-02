"""A fresh vault must work end to end without any personal setup."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from obsidiyan.graph import audit_graph
from obsidiyan.notewriter import remember
from obsidiyan.search import search
from obsidiyan.vault_init import init_vault

TODAY = date(2026, 8, 1)


def test_init_creates_a_vault_that_passes_the_graph_audit(tmp_path: Path) -> None:
    result = init_vault(tmp_path / "vault", today=TODAY)

    report = audit_graph(result.root)
    assert report.ok, report.render()
    assert (result.root / "private" / "nda-source-overrides.json").is_file()


def test_init_never_overwrites_existing_files(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    init_vault(root, today=TODAY)
    (root / "BRAIN.md").write_text("# My own brain\n", encoding="utf-8")

    second = init_vault(root, today=TODAY)

    assert second.created == []
    assert (root / "BRAIN.md").read_text(encoding="utf-8") == "# My own brain\n"


def test_remember_works_on_a_fresh_vault(tmp_path: Path) -> None:
    root = init_vault(tmp_path / "vault", today=TODAY).root

    result = remember(
        "Database choice", "Postgres is the main database.", root / "notes",
        parent="notes/projects", today=TODAY,
    )

    assert result.error == "", result.error
    assert audit_graph(root).ok


def test_demo_is_searchable_and_keeps_the_confidential_session_hidden(tmp_path: Path) -> None:
    result = init_vault(tmp_path / "vault", demo=True, today=TODAY)
    corpus = result.root / "corpus"

    assert result.demo_docs == 4
    assert [h.doc.title for h in search("Postgres", corpus, today=TODAY)]
    assert search("Acme", corpus, today=TODAY) == []
    assert search("Acme", corpus, include_nda=True, today=TODAY)
