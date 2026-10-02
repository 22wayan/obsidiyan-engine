from __future__ import annotations

from datetime import date
from pathlib import Path

from obsidiyan.notewriter import remember, remember_private

TODAY = date(2026, 8, 8)


def _parent(root: Path) -> str:
    (root / "parent.md").write_text("# Parent\n", encoding="utf-8")
    return "parent"


def test_creates_note_with_frontmatter(tmp_path: Path) -> None:
    res = remember(
        "Vertragsform bei Teamco",
        "Dienstvertrag statt Werkvertrag.",
        tmp_path,
        parent=_parent(tmp_path),
        today=TODAY,
    )
    assert res.ok and res.created
    text = res.path.read_text(encoding="utf-8")
    assert text.startswith("---\ncreated: 2026-08-08\n")
    assert "ai_generated: true" in text
    assert 'parent: "[[parent]]"' in text
    assert "# Vertragsform bei Teamco" in text
    assert res.path.name == "vertragsform-bei-teamco.md"


def test_appends_instead_of_overwriting(tmp_path: Path) -> None:
    remember("Thema", "erster Inhalt", tmp_path, parent=_parent(tmp_path), today=TODAY)
    res = remember("Thema", "zweiter Inhalt", tmp_path, section="Runde 2", today=TODAY)
    assert res.ok and res.appended and not res.created
    text = res.path.read_text(encoding="utf-8")
    assert "erster Inhalt" in text
    assert "## Runde 2" in text
    assert "zweiter Inhalt" in text


def test_append_bumps_modified_date(tmp_path: Path) -> None:
    remember("Thema", "a", tmp_path, parent=_parent(tmp_path), today=date(2026, 1, 1))
    res = remember("Thema", "b", tmp_path, today=TODAY)
    text = res.path.read_text(encoding="utf-8")
    assert "created: 2026-01-01" in text
    assert "modified: 2026-08-08" in text


def test_append_rejects_existing_note_without_parent_metadata(tmp_path: Path) -> None:
    path = tmp_path / "thema.md"
    path.write_text("# Thema\n\nmodified: 2020-01-01\n", encoding="utf-8")

    res = remember("Thema", "Nachtrag", tmp_path, today=TODAY)

    assert not res.ok
    assert "parent" in res.error
    text = path.read_text(encoding="utf-8")
    assert "modified: 2020-01-01" in text
    assert "modified: 2026-08-08" not in text


def test_append_adds_modified_to_existing_frontmatter(tmp_path: Path) -> None:
    parent = _parent(tmp_path)
    path = tmp_path / "thema.md"
    path.write_text(
        f'---\ncreated: 2026-01-01\nparent: "[[{parent}]]"\n---\n\n# Thema\n\nAlt\n',
        encoding="utf-8",
    )

    res = remember("Thema", "Neu", tmp_path, today=TODAY)

    assert res.ok
    assert "modified: 2026-08-08" in path.read_text(encoding="utf-8")


def test_refuses_nda_material(tmp_path: Path) -> None:
    """notes/ ist git-getrackt. Ein NDA-Fehler dort ist nicht mehr einzufangen."""
    res = remember("Kundenstand", "Gespraech mit Acme gelaufen", tmp_path, today=TODAY)
    assert not res.ok
    assert "NDA" in res.error
    assert not list(tmp_path.glob("*.md"))


def test_refuses_empty_input(tmp_path: Path) -> None:
    assert not remember("", "text", tmp_path, today=TODAY).ok
    assert not remember("titel", "   ", tmp_path, today=TODAY).ok


def test_clean_writer_refuses_email_in_any_field(tmp_path: Path) -> None:
    res = remember("Kontakt", "Mail an test@example.com", tmp_path, today=TODAY)
    assert not res.ok
    assert "E-Mail" in res.error
    assert not list(tmp_path.glob("*.md"))


def test_clean_writer_checks_section_for_nda(tmp_path: Path) -> None:
    res = remember(
        "Status",
        "Neutral",
        tmp_path,
        section="Acme Nachtrag",
        today=TODAY,
    )
    assert not res.ok
    assert "NDA" in res.error


def test_creates_private_note_with_forced_nda_frontmatter(tmp_path: Path) -> None:
    res = remember_private(
        "Kundenprojekt Alpha",
        "Aktueller Stand",
        "Der Pilot ist vorbereitet.",
        tmp_path,
        parent=_parent(tmp_path),
        today=TODAY,
    )
    assert res.ok and res.created
    assert res.path == tmp_path / "kundenprojekt-alpha" / "aktueller-stand.md"
    text = res.path.read_text(encoding="utf-8")
    assert "sensitivity: nda" in text
    assert "project: 'Kundenprojekt Alpha'" in text
    assert 'parent: "[[parent]]"' in text


def test_private_note_appends_instead_of_overwriting(tmp_path: Path) -> None:
    remember_private(
        "Projekt", "Status", "eins", tmp_path, parent=_parent(tmp_path), today=TODAY
    )
    res = remember_private(
        "Projekt",
        "Status",
        "zwei",
        tmp_path,
        section="Neuer Stand",
        today=TODAY,
    )
    assert res.ok and res.appended
    text = res.path.read_text(encoding="utf-8")
    assert "eins" in text and "## Neuer Stand" in text and "zwei" in text


def test_private_writer_slugifies_project_and_title(tmp_path: Path) -> None:
    res = remember_private(
        "../../Kunde",
        "../Status",
        "Inhalt",
        tmp_path,
        parent=_parent(tmp_path),
        today=TODAY,
    )
    assert res.ok
    assert tmp_path.resolve() in res.path.resolve().parents
    assert ".." not in res.path.parts


def test_private_writer_refuses_empty_input(tmp_path: Path) -> None:
    assert not remember_private("", "Titel", "Text", tmp_path, today=TODAY).ok
    assert not remember_private("Projekt", "", "Text", tmp_path, today=TODAY).ok


def test_private_writer_refuses_symlink_escape(tmp_path: Path) -> None:
    private = tmp_path / "private"
    outside = tmp_path / "outside"
    outside.mkdir()
    private.mkdir()
    (private / "projekt").symlink_to(outside, target_is_directory=True)

    res = remember_private("Projekt", "Status", "Inhalt", private, today=TODAY)

    assert not res.ok
    assert "private_root" in res.error
    assert not (outside / "status.md").exists()


def test_private_writer_refuses_file_symlink_escape(tmp_path: Path) -> None:
    private = tmp_path / "private"
    target = tmp_path / "outside.md"
    project = private / "projekt"
    project.mkdir(parents=True)
    target.write_text("bestehend", encoding="utf-8")
    (project / "status.md").symlink_to(target)

    res = remember_private("Projekt", "Status", "Inhalt", private, today=TODAY)

    assert not res.ok
    assert target.read_text(encoding="utf-8") == "bestehend"


def test_private_writer_refuses_symlinked_root(tmp_path: Path) -> None:
    tracked = tmp_path / "notes"
    tracked.mkdir()
    private = tmp_path / "private"
    private.symlink_to(tracked, target_is_directory=True)

    res = remember_private("Projekt", "Status", "Inhalt", private, today=TODAY)

    assert not res.ok
    assert "private_root" in res.error
    assert not (tracked / "projekt" / "status.md").exists()


def test_private_writer_replaces_hardlink_without_modifying_source(tmp_path: Path) -> None:
    tracked = tmp_path / "notes" / "tracked.md"
    tracked.parent.mkdir()
    private = tmp_path / "private"
    private.mkdir()
    (private / "INDEX.md").write_text("# Private Index\n", encoding="utf-8")
    tracked.write_text(
        '---\ncreated: 2026-01-01\nparent: "[[private/INDEX]]"\n---\n\n# Status\n\nbestehend\n',
        encoding="utf-8",
    )
    original = tracked.read_text(encoding="utf-8")
    target = private / "projekt" / "status.md"
    target.parent.mkdir(parents=True)
    target.hardlink_to(tracked)

    res = remember_private("Projekt", "Status", "Inhalt", private, today=TODAY)

    assert res.ok and res.appended
    assert tracked.read_text(encoding="utf-8") == original
    assert "Inhalt" in target.read_text(encoding="utf-8")
    assert target.stat().st_ino != tracked.stat().st_ino


def test_private_writer_refuses_secrets(tmp_path: Path) -> None:
    fake_secret = "sk-" + "a" * 24
    res = remember_private("Projekt", "Status", fake_secret, tmp_path, today=TODAY)
    assert not res.ok
    assert "Secret" in res.error


def test_private_writer_cannot_inject_frontmatter_or_headings(tmp_path: Path) -> None:
    res = remember_private(
        "Projekt\nsensitivity: clean",
        "Status\n# Eingeschleust",
        "Inhalt",
        tmp_path,
        section="Abschnitt\n---",
        parent=_parent(tmp_path),
        today=TODAY,
    )
    assert res.ok
    text = res.path.read_text(encoding="utf-8")
    assert "project: 'Projekt sensitivity: clean'" in text
    assert "# Status # Eingeschleust" in text
    assert "\nsensitivity: clean\n" not in text


def test_new_clean_note_requires_existing_parent(tmp_path: Path) -> None:
    missing = remember("Thema", "Inhalt", tmp_path, today=TODAY)
    unknown = remember("Thema", "Inhalt", tmp_path, parent="unbekannt", today=TODAY)

    assert not missing.ok and "parent" in missing.error
    assert not unknown.ok and "existiert nicht" in unknown.error
    assert not (tmp_path / "thema.md").exists()


def test_new_private_note_requires_existing_parent(tmp_path: Path) -> None:
    result = remember_private("Projekt", "Status", "Inhalt", tmp_path, today=TODAY)

    assert not result.ok
    assert "parent" in result.error
    assert not (tmp_path / "projekt" / "status.md").exists()


def test_parent_cannot_cross_layer_boundary(tmp_path: Path) -> None:
    notes = tmp_path / "notes"
    private = tmp_path / "private"
    notes.mkdir()
    private.mkdir()
    (private / "INDEX.md").write_text("# Privat\n", encoding="utf-8")
    (notes / "projects.md").write_text("# Projekte\n", encoding="utf-8")

    clean = remember("Thema", "Inhalt", notes, parent="private/INDEX", today=TODAY)
    nda = remember_private(
        "Projekt", "Status", "Inhalt", private, parent="notes/projects", today=TODAY
    )

    assert not clean.ok and "Layer" in clean.error
    assert not nda.ok and "Layer" in nda.error


def test_writer_rejects_arbitrary_direct_brain_child(tmp_path: Path) -> None:
    notes = tmp_path / "notes"
    notes.mkdir()
    (tmp_path / "BRAIN.md").write_text("# Brain\n", encoding="utf-8")

    result = remember("Zufall", "Inhalt", notes, parent="BRAIN", today=TODAY)

    assert not result.ok
    assert "kein erlaubter direkter BRAIN-Child" in result.error


def test_append_rejects_parent_conflict(tmp_path: Path) -> None:
    first = _parent(tmp_path)
    (tmp_path / "other.md").write_text("# Other\n", encoding="utf-8")
    created = remember("Thema", "Alt", tmp_path, parent=first, today=TODAY)

    result = remember("Thema", "Neu", tmp_path, parent="other", today=TODAY)

    assert created.ok
    assert not result.ok and "parent-Konflikt" in result.error
    assert "Neu" not in created.path.read_text(encoding="utf-8")


def test_append_rejects_case_varied_self_parent(tmp_path: Path) -> None:
    path = tmp_path / "thema.md"
    path.write_text(
        '---\ncreated: 2026-01-01\nparent: "[[THEMA]]"\n---\n\n# Thema\n',
        encoding="utf-8",
    )

    result = remember("Thema", "Neu", tmp_path, today=TODAY)

    assert not result.ok
    assert "sich selbst" in result.error
    assert "Neu" not in path.read_text(encoding="utf-8")
