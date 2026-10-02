from __future__ import annotations

from pathlib import Path

from obsidiyan import ingest
from obsidiyan.corpusio import iter_docs, read_doc
from obsidiyan.models import Role, Sensitivity, Source
from obsidiyan.search import search
from obsidiyan.sources import local_notes


def _note(root: Path, rel: str, body: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def test_note_becomes_user_authored_doc(tmp_path: Path) -> None:
    """Kuratierte Notes sind redigiert und zaehlen im Ranking als Nutzer-Aussage."""
    path = _note(
        tmp_path,
        "notes/thema.md",
        "---\ncreated: 2026-07-31\nmodified: 2026-08-08\n---\n\n# Die Beraterpyramide\n\nInhalt.\n",
    )
    doc = local_notes.parse_note(path, tmp_path)
    assert doc is not None
    assert doc.source is Source.MEMORY
    assert doc.title == "Die Beraterpyramide"
    assert doc.turns[0].role is Role.USER
    assert doc.ended_at is not None and doc.ended_at.date().isoformat() == "2026-08-08"


def test_note_without_frontmatter_still_parses(tmp_path: Path) -> None:
    path = _note(tmp_path, "notes/roh.md", "# Titel\n\nText ohne Frontmatter\n")
    doc = local_notes.parse_note(path, tmp_path)
    assert doc is not None
    assert doc.title == "Titel"
    # Kein Frontmatter-Datum, aber die Dateizeit springt ein
    assert doc.started_at is not None


def test_memory_snapshot_keeps_project(tmp_path: Path) -> None:
    path = _note(tmp_path, "memory/nebenprojekt/MEMORY.md", "# Index\n\nZeug\n")
    doc = local_notes.parse_note(path, tmp_path)
    assert doc is not None
    assert doc.project == "nebenprojekt"


def test_nda_terms_in_a_note_are_flagged(tmp_path: Path) -> None:
    path = _note(tmp_path, "notes/heikel.md", "# X\n\nGespraech mit Acme\n")
    doc = local_notes.parse_note(path, tmp_path)
    assert doc is not None
    assert doc.sensitivity is Sensitivity.NDA


def test_private_note_is_always_nda_even_with_neutral_text(tmp_path: Path) -> None:
    path = _note(
        tmp_path,
        "private/teamco/kunde.md",
        "---\nproject: Vertrauliches Projekt\nsensitivity: clean\n---\n\n"
        "# Status\n\nNeutraler Text.\n",
    )
    doc = local_notes.parse_note(path, tmp_path)
    assert doc is not None
    assert doc.project == "Vertrauliches Projekt"
    assert doc.sensitivity is Sensitivity.NDA


def test_explicit_nda_frontmatter_is_never_downgraded(tmp_path: Path) -> None:
    path = _note(
        tmp_path,
        "notes/neutral.md",
        "---\nsensitivity: nda\n---\n\n# Status\n\nNeutraler Text.\n",
    )
    doc = local_notes.parse_note(path, tmp_path)
    assert doc is not None
    assert doc.sensitivity is Sensitivity.NDA


def test_quoted_private_project_is_parsed(tmp_path: Path) -> None:
    path = _note(
        tmp_path,
        "private/teamco/kunde.md",
        "---\nproject: 'Kunde: Bereich'\n---\n\n# Status\n\nNeutral.\n",
    )
    doc = local_notes.parse_note(path, tmp_path)
    assert doc is not None
    assert doc.project == "Kunde: Bereich"


def test_private_memory_snapshot_keeps_project(tmp_path: Path) -> None:
    path = _note(tmp_path, "private/memory/kunden-repo/MEMORY.md", "# Index\n\nZeug\n")
    doc = local_notes.parse_note(path, tmp_path)
    assert doc is not None
    assert doc.project == "kunden-repo"
    assert doc.sensitivity is Sensitivity.NDA


def test_run_ingests_notes(tmp_path: Path) -> None:
    _note(tmp_path, "notes/a.md", "# A\n\ntext\n")
    _note(tmp_path, "memory/proj/b.md", "# B\n\ntext\n")
    _note(tmp_path, "private/proj/c.md", "# C\n\ntext\n")
    corpus = tmp_path / "corpus"
    stats = ingest.run(
        "memory", corpus, session_root=tmp_path, require_source_overrides=False
    )
    assert stats.written == 3
    assert stats.nda_docs == 1
    assert len(list(iter_docs(corpus))) == 3


def test_nested_private_note_is_still_forced_nda(tmp_path: Path) -> None:
    _note(tmp_path, "private/client/notes/deep/status.md", "# Status\n\nNeutraler Text\n")
    corpus = tmp_path / "corpus"

    ingest.run(
        "memory", corpus, session_root=tmp_path, require_source_overrides=False
    )

    docs = [read_doc(path) for path in iter_docs(corpus)]
    assert len(docs) == 1
    assert docs[0].conv_id == "private/client/notes/deep/status.md"
    assert docs[0].sensitivity is Sensitivity.NDA


def test_clean_to_private_move_removes_stale_clean_corpus_doc(tmp_path: Path) -> None:
    clean = _note(tmp_path, "notes/status.md", "# Projektstatus\n\nPilot vorbereitet\n")
    corpus = tmp_path / "corpus"
    ingest.run(
        "memory", corpus, session_root=tmp_path, require_source_overrides=False
    )
    assert len(search("Pilot vorbereitet", corpus)) == 1

    clean.unlink()
    _note(
        tmp_path,
        "private/kunde/status.md",
        "# Projektstatus\n\nPilot vorbereitet\n",
    )
    stats = ingest.run(
        "memory", corpus, session_root=tmp_path, require_source_overrides=False
    )

    docs = [read_doc(path) for path in iter_docs(corpus)]
    assert len(docs) == 1
    assert docs[0].sensitivity is Sensitivity.NDA
    assert stats.removed_stale == 1
    assert search("Pilot vorbereitet", corpus) == []
    assert len(search("Pilot vorbereitet", corpus, include_nda=True)) == 1


def test_limited_memory_ingest_does_not_reconcile_unprocessed_docs(tmp_path: Path) -> None:
    _note(tmp_path, "notes/a.md", "# A\n\nAlpha\n")
    second = _note(tmp_path, "notes/b.md", "# B\n\nBeta\n")
    corpus = tmp_path / "corpus"
    ingest.run(
        "memory", corpus, session_root=tmp_path, require_source_overrides=False
    )
    second.unlink()

    stats = ingest.run(
        "memory",
        corpus,
        session_root=tmp_path,
        limit=1,
        require_source_overrides=False,
    )

    assert stats.removed_stale == 0
    assert len(list(iter_docs(corpus))) == 2


def test_note_without_dates_falls_back_to_file_mtime(tmp_path: Path) -> None:
    """41 von 52 Auto-Memory-Snapshots haben kein Frontmatter-Datum.
    Ohne Fallback ist die datumsbasierte Konfliktaufloesung fuer sie blind."""
    import os
    import time

    path = _note(tmp_path, "memory/proj/ohne-datum.md", "---\nname: x\n---\n\n# Titel\n\nText\n")
    stamp = time.mktime((2026, 3, 14, 12, 0, 0, 0, 0, -1))
    os.utime(path, (stamp, stamp))

    doc = local_notes.parse_note(path, tmp_path)
    assert doc is not None
    assert doc.started_at is not None
    assert doc.started_at.date().isoformat() == "2026-03-14"


def test_frontmatter_date_wins_over_mtime(tmp_path: Path) -> None:
    path = _note(tmp_path, "notes/mit-datum.md", "---\ncreated: 2025-01-02\n---\n\n# T\n\nText\n")
    doc = local_notes.parse_note(path, tmp_path)
    assert doc is not None
    assert doc.started_at is not None
    assert doc.started_at.date().isoformat() == "2025-01-02"
