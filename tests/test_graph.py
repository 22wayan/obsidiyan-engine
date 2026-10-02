from __future__ import annotations

import json
from pathlib import Path

from obsidiyan.graph import audit_graph


def _vault(tmp_path: Path) -> Path:
    (tmp_path / ".obsidian").mkdir()
    (tmp_path / ".obsidian" / "app.json").write_text(
        json.dumps({"userIgnoreFilters": ["memory/", "private/memory/"]}),
        encoding="utf-8",
    )
    (tmp_path / "notes").mkdir()
    (tmp_path / "BRAIN.md").write_text("# Brain\n", encoding="utf-8")
    return tmp_path


def _note(root: Path, relative: str, parent: str | None, body: str = "") -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    parent_line = f'parent: "[[{parent}]]"\n' if parent is not None else ""
    path.write_text(
        f"---\ncreated: 2026-08-09\n{parent_line}---\n\n# Note\n\n{body}\n",
        encoding="utf-8",
    )


def test_valid_curated_tree_reaches_brain(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    _note(root, "notes/projects.md", "BRAIN")
    _note(root, "notes/product.md", "notes/projects", "[[projects]]")

    report = audit_graph(root)

    assert report.ok, report.render()
    assert report.nodes == 3
    assert report.parent_edges == 2
    assert report.components == 1


def test_private_layer_can_be_absent_in_clean_clone(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    (root / "BRAIN.md").write_text("Privat: [[private/INDEX]]\n", encoding="utf-8")
    _note(root, "notes/projects.md", "BRAIN")

    assert audit_graph(root).ok


def test_private_index_must_exist_when_private_layer_exists(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    (root / "BRAIN.md").write_text("Privat: [[private/INDEX]]\n", encoding="utf-8")
    (root / "private").mkdir()

    assert "fehlendes Ziel [[private/INDEX]]" in audit_graph(root).render()


def test_missing_and_broken_parent_are_rejected(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    _note(root, "notes/orphan.md", None)
    _note(root, "notes/lost.md", "notes/missing")

    report = audit_graph(root)

    assert not report.ok
    assert "notes/orphan: genau ein parent ist Pflicht" in report.render()
    assert "notes/lost: parent fehlendes Ziel" in report.render()


def test_parent_cycle_is_rejected(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    _note(root, "notes/a.md", "notes/b")
    _note(root, "notes/b.md", "notes/a")

    assert "parent-Zyklus" in audit_graph(root).render()


def test_only_canonical_hubs_can_be_direct_brain_children(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    _note(root, "notes/random.md", "BRAIN")

    assert "kein erlaubter direkter BRAIN-Child" in audit_graph(root).render()


def test_clean_and_private_layers_cannot_be_cross_parented(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    _note(root, "private/INDEX.md", "BRAIN")
    _note(root, "notes/clean.md", "private/INDEX")
    _note(root, "private/customer.md", "notes/clean")

    rendered = audit_graph(root).render()
    assert "Clean-Node darf keinen privaten parent haben" in rendered
    assert "Private-Node darf nur BRAIN oder private/ als parent haben" in rendered


def test_broken_visible_link_fails_but_code_example_does_not(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    _note(root, "notes/projects.md", "BRAIN")
    _note(
        root,
        "notes/good.md",
        "notes/projects",
        "Beispiel: `[[not-a-node]]`",
    )
    assert audit_graph(root).ok

    _note(root, "notes/bad.md", "notes/projects", "Siehe [[not-a-node]].")
    assert "fehlendes Ziel [[not-a-node]]" in audit_graph(root).render()


def test_multiline_wikilink_is_rejected(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    _note(root, "notes/projects.md", "BRAIN")
    _note(
        root,
        "notes/bad.md",
        "notes/projects",
        "Siehe [[notes/projects|Projekt\nportfolio]].",
    )

    assert "mehrzeiliger Wikilink" in audit_graph(root).render()


def test_duplicate_node_names_are_rejected(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    _note(root, "notes/status.md", "BRAIN")
    _note(root, "private/status.md", "BRAIN")

    assert "doppelter Node-Name" in audit_graph(root).render()


def test_empty_root_placeholder_is_rejected(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    (root / "placeholder.md").touch()

    assert "leerer Root-Platzhalter" in audit_graph(root).render()


def test_visible_markdown_outside_curated_layers_is_rejected(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    outside = root / "Podcasts" / "episode.md"
    outside.parent.mkdir()
    outside.write_text("# Insel\n", encoding="utf-8")

    assert "ausserhalb des kuratierten Graphen" in audit_graph(root).render()


def test_raw_memory_ignore_filters_are_required(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    (root / ".obsidian" / "app.json").write_text(
        json.dumps({"userIgnoreFilters": ["corpus/"]}), encoding="utf-8"
    )

    rendered = audit_graph(root).render()
    assert "Ignore-Filter fehlen" in rendered
    assert "memory/" in rendered


def test_curated_layer_symlink_is_rejected(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (outside / "external.md").write_text(
        "# Extern\n\n[[sensitive-link-name]]\n", encoding="utf-8"
    )
    (root / "notes").rmdir()
    (root / "notes").symlink_to(outside, target_is_directory=True)

    rendered = audit_graph(root).render()
    assert "notes/ darf kein Symlink sein" in rendered
    assert "sensitive-link-name" not in rendered


def test_curated_file_symlink_is_rejected_without_reading_target(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    outside = tmp_path.parent / f"{tmp_path.name}-external.md"
    outside.write_text("# Extern\n\n[[sensitive-link-name]]\n", encoding="utf-8")
    (root / "notes" / "linked.md").symlink_to(outside)

    rendered = audit_graph(root).render()
    assert "kuratierter Layer-Eintrag darf kein Symlink sein: notes/linked" in rendered
    assert "sensitive-link-name" not in rendered
