from __future__ import annotations

import os
import subprocess
from pathlib import Path


def _memory_file(home: Path, project: str, name: str, body: str) -> Path:
    path = home / ".claude" / "projects" / f"-Users-demo-{project}" / "memory" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _mark_known_clean(home: Path, project: str) -> None:
    """Ein Projekt als bereits bekannt markieren.

    Der Sync behandelt unbekannte Projekte vorsorglich als vertraulich. Tests,
    die die inhaltliche Aufteilung INNERHALB eines bekannten Projekts pruefen,
    muessen es deshalb vorher anlegen, sonst pruefen sie nur noch den
    Unbekannt-Default.
    """
    (home / "Obsidiyan" / "memory" / project).mkdir(parents=True, exist_ok=True)


def _run(repo_root: Path, home: Path) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "HOME": str(home), "OBSIDIYAN_HOME": str(home / "Obsidiyan")}
    return subprocess.run(
        ["bash", str(repo_root / "bin" / "sync-memory.sh")],
        cwd=repo_root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


def test_sync_splits_clean_entity_and_denied_project(repo_root: Path, tmp_path: Path) -> None:
    _mark_known_clean(tmp_path, "clean-project")
    _memory_file(tmp_path, "clean-project", "clean.md", "Allgemeines Wissen")
    _memory_file(tmp_path, "clean-project", "kunde.md", "Termin mit ini.tech")
    _memory_file(tmp_path, "clean-project", "kontakt.md", "Kontakt: test@example.com")
    _memory_file(tmp_path, "acme-poc", "neutral.md", "Neutraler Text")
    _memory_file(tmp_path, "Acme-Poc-Mixed", "neutral.md", "Neutraler Text")

    result = _run(repo_root, tmp_path)

    assert result.returncode == 0, result.stderr
    brain = tmp_path / "Obsidiyan"
    assert (brain / "memory" / "clean-project" / "clean.md").is_file()
    assert (brain / "private" / "memory" / "clean-project" / "kunde.md").is_file()
    assert (brain / "private" / "memory" / "clean-project" / "kontakt.md").is_file()
    assert (brain / "private" / "memory" / "acme-poc" / "neutral.md").is_file()
    assert (
        brain / "private" / "memory" / "Acme-Poc-Mixed" / "neutral.md"
    ).is_file()
    assert not (brain / "memory" / "clean-project" / "kunde.md").exists()
    assert not (brain / "memory" / "clean-project" / "kontakt.md").exists()
    assert _run(repo_root, tmp_path).returncode == 0


def test_sync_fails_if_sensitive_memory_already_exists_in_clean_layer(
    repo_root: Path,
    tmp_path: Path,
) -> None:
    _memory_file(tmp_path, "clean-project", "kunde.md", "Termin mit ini.tech")
    leaked = tmp_path / "Obsidiyan" / "memory" / "clean-project" / "kunde.md"
    leaked.parent.mkdir(parents=True, exist_ok=True)
    leaked.write_text("alter clean Stand", encoding="utf-8")

    result = _run(repo_root, tmp_path)

    assert result.returncode == 1
    assert "NDA-Memory liegt noch im clean-Layer" in result.stderr


def test_sync_stops_before_copying_a_secret(repo_root: Path, tmp_path: Path) -> None:
    fake_secret = "sk-" + "a" * 24
    _memory_file(tmp_path, "clean-project", "secret.md", f"Token: {fake_secret}")

    result = _run(repo_root, tmp_path)

    assert result.returncode == 1
    assert "moegliches Secret" in result.stderr
    assert not (tmp_path / "Obsidiyan" / "memory" / "clean-project" / "secret.md").exists()
    assert not (
        tmp_path / "Obsidiyan" / "private" / "memory" / "clean-project" / "secret.md"
    ).exists()


def test_sync_refuses_symlinked_private_root(repo_root: Path, tmp_path: Path) -> None:
    _memory_file(tmp_path, "acme-poc", "status.md", "Neutraler Text")
    brain = tmp_path / "Obsidiyan"
    tracked = brain / "notes"
    tracked.mkdir(parents=True)
    (brain / "private").symlink_to(tracked, target_is_directory=True)

    result = _run(repo_root, tmp_path)

    assert result.returncode == 1
    assert "unsicherer Brain-Zielpfad" in result.stderr
    assert not (tracked / "memory" / "acme-poc" / "status.md").exists()


def test_sync_refuses_symlinked_private_component(repo_root: Path, tmp_path: Path) -> None:
    _memory_file(tmp_path, "acme-poc", "status.md", "Neutraler Text")
    brain = tmp_path / "Obsidiyan"
    tracked = brain / "notes"
    tracked.mkdir(parents=True)
    private_memory = brain / "private" / "memory"
    private_memory.mkdir(parents=True)
    (private_memory / "acme-poc").symlink_to(tracked, target_is_directory=True)

    result = _run(repo_root, tmp_path)

    assert result.returncode == 1
    assert "unsicherer Brain-Zielpfad" in result.stderr
    assert not (tracked / "status.md").exists()


def test_sync_refuses_symlinked_source_file(repo_root: Path, tmp_path: Path) -> None:
    external = tmp_path / "external.md"
    external.write_text("Allgemeines Wissen", encoding="utf-8")
    source = _memory_file(tmp_path, "clean-project", "placeholder.md", "wird ersetzt")
    source.unlink()
    source.symlink_to(external)

    result = _run(repo_root, tmp_path)

    assert result.returncode == 1
    assert "Quelle ist ein Symlink" in result.stderr
    assert not (tmp_path / "Obsidiyan" / "memory" / "clean-project").exists()


def test_sync_refuses_symlinked_source_directory(repo_root: Path, tmp_path: Path) -> None:
    external = tmp_path / "external-memory"
    external.mkdir()
    (external / "status.md").write_text("Allgemeines Wissen", encoding="utf-8")
    project = tmp_path / ".claude" / "projects" / "-Users-demo-clean-project"
    project.mkdir(parents=True)
    (project / "memory").symlink_to(external, target_is_directory=True)

    result = _run(repo_root, tmp_path)

    assert result.returncode == 1
    assert "unsicherer Auto-Memory-Quellpfad" in result.stderr
    assert not (tmp_path / "Obsidiyan" / "memory" / "clean-project").exists()


def test_sync_replaces_hardlink_without_modifying_source(
    repo_root: Path,
    tmp_path: Path,
) -> None:
    _memory_file(tmp_path, "acme-poc", "status.md", "Neuer privater Stand")
    tracked = tmp_path / "Obsidiyan" / "notes" / "tracked.md"
    tracked.parent.mkdir(parents=True)
    tracked.write_text("bestehend", encoding="utf-8")
    target = (
        tmp_path
        / "Obsidiyan"
        / "private"
        / "memory"
        / "acme-poc"
        / "status.md"
    )
    target.parent.mkdir(parents=True)
    target.hardlink_to(tracked)

    result = _run(repo_root, tmp_path)

    assert result.returncode == 0, result.stderr
    assert tracked.read_text(encoding="utf-8") == "bestehend"
    assert target.read_text(encoding="utf-8") == "Neuer privater Stand"
    assert target.stat().st_ino != tracked.stat().st_ino


def test_unknown_project_defaults_to_private(repo_root: Path, tmp_path: Path) -> None:
    """Ein Projekt, das der Sync noch nie gesehen hat, wird vorsorglich als
    vertraulich behandelt. Der teure Fehler ist Kundenmaterial im getrackten
    Layer, nicht ein eigenes Projekt einen Lauf lang im privaten."""
    _memory_file(tmp_path, "brandneuer-kunde", "notiz.md", "Voellig harmloser Satz")

    result = _run(repo_root, tmp_path)

    assert result.returncode == 0, result.stderr
    brain = tmp_path / "Obsidiyan"
    assert (brain / "private" / "memory" / "brandneuer-kunde" / "notiz.md").is_file()
    assert not (brain / "memory" / "brandneuer-kunde" / "notiz.md").exists()
    assert "brandneuer-kunde" in result.stdout


def test_known_project_stays_clean(repo_root: Path, tmp_path: Path) -> None:
    """Die Umkehr gilt nur fuer Neulinge. Ein bereits bekanntes Projekt
    behaelt den clean-Pfad, sonst waere jeder Lauf ein Rueckschritt."""
    _mark_known_clean(tmp_path, "eigenes-projekt")
    _memory_file(tmp_path, "eigenes-projekt", "notiz.md", "Harmloser Satz")

    result = _run(repo_root, tmp_path)

    assert result.returncode == 0, result.stderr
    brain = tmp_path / "Obsidiyan"
    assert (brain / "memory" / "eigenes-projekt" / "notiz.md").is_file()
    assert not (brain / "private" / "memory" / "eigenes-projekt" / "notiz.md").exists()
