"""Die Vault-Wurzel darf nicht mehr an der Paket-Position haengen.

Regression: in einem Conductor-Worktree lag eine zweite Kopie des Pakets ohne
``corpus/``. Der MCP-Server importierte diese Kopie, suchte im leeren Worktree
und lieferte null Treffer ohne Fehlermeldung.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from obsidiyan import mcp_server, paths


def test_env_var_wins_over_package_position(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(paths.ENV_VAR, str(tmp_path))
    assert paths.vault_root() == tmp_path.resolve()
    assert paths.corpus_root() == tmp_path.resolve() / "corpus"
    assert paths.notes_root() == tmp_path.resolve() / "notes"


def test_without_env_var_the_package_parent_stays_the_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(paths.ENV_VAR, raising=False)
    assert paths.vault_root() == paths.PACKAGE_PARENT


def test_pointing_at_nothing_fails_instead_of_guessing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(paths.ENV_VAR, str(tmp_path / "gibt-es-nicht"))
    with pytest.raises(paths.VaultError):
        paths.vault_root()


def test_missing_corpus_raises_instead_of_returning_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mcp_server, "CORPUS_ROOT", tmp_path / "corpus")
    monkeypatch.setattr(mcp_server, "REPO_ROOT", tmp_path)

    with pytest.raises(paths.VaultError) as excinfo:
        mcp_server.search("egal")

    assert paths.ENV_VAR in str(excinfo.value)


def test_module_constants_follow_the_env_var_on_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(paths.ENV_VAR, str(tmp_path))
    vault = tmp_path.resolve()
    reloaded = importlib.reload(mcp_server)
    try:
        assert vault == reloaded.REPO_ROOT
        assert vault / "corpus" == reloaded.CORPUS_ROOT
    finally:
        monkeypatch.delenv(paths.ENV_VAR, raising=False)
        importlib.reload(mcp_server)


def test_vault_supplies_its_own_mcp_instructions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "mcp-instructions.md").write_text("Team-Vault, geteilt.\n", encoding="utf-8")
    monkeypatch.setattr(mcp_server, "REPO_ROOT", tmp_path)

    assert mcp_server._instructions() == "Team-Vault, geteilt."


def test_instructions_fall_back_when_the_vault_says_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mcp_server, "REPO_ROOT", tmp_path)

    assert mcp_server._instructions() == mcp_server._DEFAULT_INSTRUCTIONS
