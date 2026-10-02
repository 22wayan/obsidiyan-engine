"""Kursmaterial muss auffindbar sein und trotzdem nie in notes/ landen.

Die drei Eigenschaften, an denen das haengt, sind hier einzeln festgenagelt:
COPYRIGHT statt CLEAN, kein Nutzer-Turn, und damit kein Destillat-Kandidat.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from obsidiyan import ingest
from obsidiyan.distill.select import select, user_chars
from obsidiyan.models import Role, Sensitivity, Source
from obsidiyan.search import search
from obsidiyan.sources import courses

TRANSCRIPT = """---
source: course
course: 'wall-street-quants'
module: '12.Weighting'
lesson: '3. Risk Parity'
media_path: '12.Weighting/3. Risk Parity.mp4'
duration_seconds: 900
asr_model: 'mlx-community/whisper-large-v3-turbo'
sensitivity: copyright
---

# 3. Risk Parity

Welcome back. Today we look at risk parity portfolios and how the weights
follow from the inverse of each asset's volatility contribution.
"""


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    lesson = tmp_path / "courses" / "wall-street-quants" / "12.Weighting" / "3. Risk Parity.md"
    lesson.parent.mkdir(parents=True)
    lesson.write_text(TRANSCRIPT, encoding="utf-8")
    (lesson.parent.parent / ".manifest.tsv").write_text("status\tseconds\n", encoding="utf-8")
    return tmp_path


def test_transcript_becomes_a_doc(vault: Path) -> None:
    path = next(courses.iter_courses(vault))
    doc = courses.parse_course(path, vault)

    assert doc is not None
    assert doc.source is Source.COURSE
    assert doc.project == "wall-street-quants"
    assert "Risk Parity" in doc.title
    assert "risk parity portfolios" in doc.turns[0].text


def test_module_is_part_of_the_title(vault: Path) -> None:
    """Ohne Modul sind "Part 2"-Lektionen aus zwei Kursen nicht unterscheidbar."""
    doc = courses.parse_course(next(courses.iter_courses(vault)), vault)

    assert doc is not None
    assert doc.title.startswith("12.Weighting: ")


def test_the_layer_decides_the_sensitivity_not_the_frontmatter(vault: Path) -> None:
    lesson = vault / "courses" / "wall-street-quants" / "gelogen.md"
    lesson.write_text(
        "---\nsource: course\nsensitivity: clean\n---\n\n# Egal\n\nText.\n", encoding="utf-8"
    )

    doc = courses.parse_course(lesson, vault)

    assert doc is not None
    assert doc.sensitivity is Sensitivity.COPYRIGHT


def test_a_lecture_has_no_user_turn(vault: Path) -> None:
    """Der strukturelle Riegel: die Kandidatenauswahl wertet nur Nutzertext."""
    doc = courses.parse_course(next(courses.iter_courses(vault)), vault)

    assert doc is not None
    assert all(turn.role is Role.ASSISTANT for turn in doc.turns)
    assert user_chars(doc) == 0


def test_only_markdown_is_ingested(vault: Path) -> None:
    (vault / "courses" / "wall-street-quants" / "notebook.ipynb").write_text("{}", encoding="utf-8")

    found = [p.name for p in courses.iter_courses(vault)]

    assert found == ["3. Risk Parity.md"]


def test_empty_transcript_is_skipped(vault: Path) -> None:
    empty = vault / "courses" / "wall-street-quants" / "leer.md"
    empty.write_text("---\nsource: course\n---\n\n", encoding="utf-8")

    assert courses.parse_course(empty, vault) is None


def test_ingested_course_is_searchable(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    corpus = vault / "corpus"
    monkeypatch.setattr(ingest, "REPO_ROOT", vault)

    ingest.run("course", corpus, require_source_overrides=False)
    hits = search("risk parity", corpus)

    assert [h.doc.title for h in hits] == ["12.Weighting: 3. Risk Parity"]
    assert hits[0].doc.sensitivity is Sensitivity.COPYRIGHT


def test_course_material_never_becomes_a_distillation_candidate(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der explizite Riegel, zusaetzlich zum strukturellen oben.

    notes/ ist git-getrackt. Fremdes Kursmaterial darf dort nicht als eigenes
    Wissen landen, auch nicht als Maschinen-Zusammenfassung.
    """
    corpus = vault / "corpus"
    monkeypatch.setattr(ingest, "REPO_ROOT", vault)
    ingest.run("course", corpus, require_source_overrides=False)

    assert select(corpus, min_user_chars=0) == []
