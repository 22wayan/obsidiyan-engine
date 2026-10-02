from __future__ import annotations

from pathlib import Path

import pytest
from conftest import make_doc

from obsidiyan import ingest as ingest_module
from obsidiyan import mcp_server
from obsidiyan.corpusio import write_doc
from obsidiyan.models import Sensitivity


@pytest.fixture(autouse=True)
def corpus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    write_doc(make_doc(conv_id="offen", title="Offen", sensitivity=Sensitivity.CLEAN), tmp_path)
    write_doc(make_doc(conv_id="geheim", title="Geheim", sensitivity=Sensitivity.NDA), tmp_path)
    monkeypatch.setattr(mcp_server, "CORPUS_ROOT", tmp_path)
    return tmp_path


def test_search_hides_nda_by_default() -> None:
    rows = mcp_server.search("Entscheidung")
    assert [r["title"] for r in rows] == ["Offen"]


def test_search_exposes_nda_only_on_request() -> None:
    titles = {r["title"] for r in mcp_server.search("Entscheidung", include_nda=True)}
    assert titles == {"Offen", "Geheim"}


def test_get_doc_refuses_nda_without_scope() -> None:
    doc_id = mcp_server.search("Entscheidung", include_nda=True)
    nda_id = next(r["doc_id"] for r in doc_id if r["sensitivity"] == "nda")
    assert "error" in mcp_server.get_doc(nda_id)
    assert "text" in mcp_server.get_doc(nda_id, include_nda=True)


def test_get_doc_rejects_path_traversal() -> None:
    assert "error" in mcp_server.get_doc("../../etc/passwd")


def test_timeline_hides_nda_by_default() -> None:
    assert [r["title"] for r in mcp_server.timeline()] == ["Offen"]


def test_tools_are_registered() -> None:
    names = {t.name for t in mcp_server.server._tool_manager.list_tools()}
    assert {"search", "get_doc", "timeline", "remember", "remember_private"} <= names


def test_writer_tools_expose_parent_to_every_mcp_client() -> None:
    tools = {tool.name: tool for tool in mcp_server.server._tool_manager.list_tools()}

    for name in ("remember", "remember_private"):
        assert "parent" in tools[name].parameters["properties"]
        assert "parent" in (tools[name].description or "")


def test_remember_private_writes_and_ingests(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private = tmp_path / "private"
    private.mkdir()
    (private / "INDEX.md").write_text("# Private Index\n", encoding="utf-8")
    calls: list[tuple[str, Path, bool]] = []
    monkeypatch.setattr(mcp_server, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(mcp_server, "PRIVATE_ROOT", private)
    monkeypatch.setattr(
        ingest_module,
        "run",
        lambda source, root, *, require_source_overrides=False: calls.append(
            (source, root, require_source_overrides)
        ),
    )

    result = mcp_server.remember_private(
        "Projekt", "Status", "Aktueller Stand", parent="private/INDEX"
    )

    assert result["created"] is True
    assert result["sensitivity"] == "nda"
    assert result["path"] == "private/projekt/status.md"
    assert calls == [("memory", tmp_path, False)]
