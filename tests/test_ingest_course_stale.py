"""Ein umdatiertes Kursdokument darf keine zweite Datei hinterlassen.

Am 2026-09-16 lagen in corpus/course 35.575 Dateien fuer 2.882 Dokumente,
jede Kurz-ID genau 22 Mal, je eine pro Tag seit dem 24.08. Der Vault ist
dadurch auf 40.893 Markdown-Dateien angewachsen, und Obsidian brauchte beim
Start Minuten.

Die Kette dahinter: scripts/build-course-graph.py schreibt jede Kursdatei bei
jedem Lauf neu, um die parent-Zeile zu setzen. Das hebt die mtime.
sources/courses.py leitet das Datum des Dokuments aus genau dieser mtime ab,
also wandert der Corpus-Pfad taeglich. Und ingest raeumte nur Memory-Docs
auf, nie Kurs-Docs.

Jede der drei Stellen fuer sich ist vertretbar. Zusammen erzeugen sie 1.600
Dateien pro Tag. Der Riegel gehoert dorthin, wo er unabhaengig von den
anderen beiden haelt: was nicht mehr zu einer aktuellen Quelldatei gehoert,
wird entfernt.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from obsidiyan import ingest
from obsidiyan.corpusio import iter_docs

TRANSCRIPT = """---
source: course
course: 'wall-street-quants'
module: '01.Intro'
lesson: '1. Start'
sensitivity: copyright
---

# 1. Start

Welcome to the course. Today we cover the basics of portfolio construction.
"""


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    lektion = tmp_path / "courses" / "wall-street-quants" / "01.Intro" / "1. Start.md"
    lektion.parent.mkdir(parents=True)
    lektion.write_text(TRANSCRIPT, encoding="utf-8")
    return tmp_path


def _kurs_docs(corpus: Path) -> list[Path]:
    return sorted(iter_docs(corpus / "course"))


def test_umdatieren_hinterlaesst_keine_zweite_datei(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus = vault / "corpus"
    monkeypatch.setattr(ingest, "REPO_ROOT", vault)
    lektion = next((vault / "courses").rglob("*.md"))

    alt = time.time() - 60 * 60 * 24 * 10
    os.utime(lektion, (alt, alt))
    ingest.run("course", corpus, require_source_overrides=False)
    assert len(_kurs_docs(corpus)) == 1
    erster = _kurs_docs(corpus)[0]

    # build-course-graph.py schreibt die Datei neu, die mtime springt auf heute.
    lektion.write_text(TRANSCRIPT, encoding="utf-8")
    jetzt = time.time()
    os.utime(lektion, (jetzt, jetzt))
    ingest.run("course", corpus, require_source_overrides=False)

    docs = _kurs_docs(corpus)
    assert len(docs) == 1, f"erwartet 1 Dokument, gefunden {[p.name for p in docs]}"
    assert docs[0] != erster, "das Datum im Pfad soll wandern, nur die alte Datei muss weg"


def test_geloeschte_lektion_verschwindet_aus_dem_corpus(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus = vault / "corpus"
    monkeypatch.setattr(ingest, "REPO_ROOT", vault)
    ingest.run("course", corpus, require_source_overrides=False)
    assert len(_kurs_docs(corpus)) == 1

    next((vault / "courses").rglob("*.md")).unlink()
    ingest.run("course", corpus, require_source_overrides=False)

    assert _kurs_docs(corpus) == []


def test_zweite_lektion_bleibt_erhalten(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Die Gegenprobe: das Aufraeumen darf nicht zu viel wegnehmen."""
    corpus = vault / "corpus"
    monkeypatch.setattr(ingest, "REPO_ROOT", vault)
    zweite = vault / "courses" / "wall-street-quants" / "01.Intro" / "2. Weiter.md"
    zweite.write_text(TRANSCRIPT.replace("1. Start", "2. Weiter"), encoding="utf-8")

    ingest.run("course", corpus, require_source_overrides=False)
    ingest.run("course", corpus, require_source_overrides=False)

    assert len(_kurs_docs(corpus)) == 2
