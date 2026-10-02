"""Orchestrierung des Ingests: Quelle lesen, destillieren, in den Corpus schreiben.

Ein Adapter liefert pro Datei beliebig viele Docs. Session-Transkripte sind der
Sonderfall "genau eins", Provider-Archive enthalten hunderte pro Datei. Beides
laeuft deshalb durch denselben Pfad statt durch zwei.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path

from obsidiyan import paths
from obsidiyan.corpusio import doc_path, iter_docs, write_doc
from obsidiyan.models import Doc, Sensitivity, Source
from obsidiyan.nda import (
    DEFAULT_SOURCE_OVERRIDES,
    load_source_overrides,
    policy_signature,
    source_is_denied,
)
from obsidiyan.sources import archives, claude_code, codex, courses, local_notes
from obsidiyan.watermark import Watermark

REPO_ROOT = paths.vault_root()
DEFAULT_CHATS = paths.chats_root()
INGEST_POLICY_VERSION = "3"

Parser = Callable[[Path], Iterable[Doc]]
Lister = Callable[[Path | None], Iterator[Path]]


def _single(parse_one: Callable[[Path], Doc | None]) -> Parser:
    """Ein-Doc-Parser auf die Multi-Doc-Signatur heben."""

    def wrapped(path: Path) -> Iterable[Doc]:
        doc = parse_one(path)
        return [] if doc is None else [doc]

    return wrapped


def _list_notes(root: Path | None) -> Iterator[Path]:
    return local_notes.iter_notes(root or REPO_ROOT)


def _parse_note(path: Path) -> Doc | None:
    return local_notes.parse_note(path, REPO_ROOT)


def _list_courses(root: Path | None) -> Iterator[Path]:
    return courses.iter_courses(root or REPO_ROOT)


def _parse_course(path: Path) -> Doc | None:
    return courses.parse_course(path, REPO_ROOT)


def _list_archive(source: Source, root: Path | None) -> Iterator[Path]:
    return archives.iter_archive_files(source, root or DEFAULT_CHATS)


@dataclass(frozen=True)
class Adapter:
    source: Source
    list_sessions: Lister
    parse: Parser


def _archive_adapter(source: Source) -> Adapter:
    _, parser = archives.PARSERS[source]
    return Adapter(source, partial(_list_archive, source), parser)


ADAPTERS: dict[str, Adapter] = {
    Source.CLAUDE_CODE.value: Adapter(
        Source.CLAUDE_CODE, claude_code.iter_sessions, _single(claude_code.parse_session)
    ),
    Source.CODEX.value: Adapter(Source.CODEX, codex.iter_sessions, _single(codex.parse_session)),
    Source.MEMORY.value: Adapter(Source.MEMORY, _list_notes, _single(_parse_note)),
    Source.COURSE.value: Adapter(Source.COURSE, _list_courses, _single(_parse_course)),
    Source.CHATGPT.value: _archive_adapter(Source.CHATGPT),
    Source.GEMINI.value: _archive_adapter(Source.GEMINI),
    Source.CLAUDE_WEB.value: _archive_adapter(Source.CLAUDE_WEB),
}


@dataclass(frozen=True)
class Stats:
    """Ergebnis eines Laufs. nda_docs wird bewusst mitgezaehlt, damit der Split pruefbar ist."""

    files_seen: int = 0
    skipped_unchanged: int = 0
    skipped_empty: int = 0
    written: int = 0
    nda_docs: int = 0
    clean_docs: int = 0
    copyright_docs: int = 0
    removed_stale: int = 0

    def bump(self, **kw: int) -> Stats:
        return replace(self, **{k: getattr(self, k) + v for k, v in kw.items()})

    def render(self) -> str:
        return (
            f"dateien={self.files_seen} unveraendert={self.skipped_unchanged} "
            f"leer={self.skipped_empty} docs={self.written} "
            f"(clean={self.clean_docs} copyright={self.copyright_docs} "
            f"nda={self.nda_docs}) "
            f"veraltet_entfernt={self.removed_stale}"
        )


def _remove_stale_docs(corpus_root: Path, source: Source, expected: set[Path]) -> int:
    """Entfernt abgeleitete Docs, deren Quelldatei verschoben oder geloescht wurde.

    Gilt fuer die Quellen, deren Corpus-Pfad sich aus der Dateizeit ableitet,
    also Memory und Kurse. Schreibt ein Skript die Quelldatei neu, springt die
    mtime, das Datum im Pfad wandert, und ohne dieses Aufraeumen bleibt die
    alte Datei liegen.

    Am 2026-09-16 lagen deshalb 35.575 Kurs-Dateien fuer 2.882 Dokumente im
    Corpus, jede Kurz-ID genau 22 Mal, je eine pro Tag seit dem ersten Ingest.
    Der Vault kam auf 40.893 Markdown-Dateien, und Obsidian brauchte beim
    Start Minuten.
    """
    removed = 0
    for path in iter_docs(corpus_root / source.value):
        if path in expected:
            continue
        path.unlink()
        removed += 1
    return removed


def run(
    source: str,
    corpus_root: Path,
    *,
    session_root: Path | None = None,
    dry_run: bool = False,
    limit: int | None = None,
    source_overrides_path: Path | None = None,
    require_source_overrides: bool = True,
) -> Stats:
    adapter = ADAPTERS[source]
    overrides = load_source_overrides(
        source_overrides_path or DEFAULT_SOURCE_OVERRIDES,
        required=require_source_overrides,
    )
    version = f"{INGEST_POLICY_VERSION}:{policy_signature(overrides)}"
    mark = Watermark(corpus_root / ".watermark.json", policy_version=version)
    stats = Stats()
    paths = list(adapter.list_sessions(session_root))
    parser = adapter.parse
    # Quellen, deren Corpus-Pfad aus der Dateizeit stammt: wandert die mtime,
    # wandert der Pfad, und die alte Datei muss weg.
    expected_paths: set[Path] | None = None

    if adapter.source is Source.COURSE:
        parsed_courses = {path: adapter.parse(path) for path in paths}

        def parse_course(path: Path) -> Iterable[Doc]:
            return parsed_courses[path]

        parser = parse_course
        expected_paths = {
            doc_path(doc, corpus_root)
            for docs in parsed_courses.values()
            for doc in docs
            if not doc.is_empty
        }

    if adapter.source is Source.MEMORY:
        notes_root = session_root or REPO_ROOT
        parsed_notes = {path: local_notes.parse_note(path, notes_root) for path in paths}

        def parse_memory(path: Path) -> Iterable[Doc]:
            doc = parsed_notes[path]
            return () if doc is None else (doc,)

        parser = parse_memory
        expected_paths = {
            doc_path(doc, corpus_root)
            for doc in parsed_notes.values()
            if doc is not None and not doc.is_empty
        }

    for path in paths:
        if limit is not None and stats.files_seen >= limit:
            break
        stats = stats.bump(files_seen=1)

        if not mark.is_fresh(path):
            stats = stats.bump(skipped_unchanged=1)
            continue

        wrote_any = False
        for doc in parser(path):
            if doc.is_empty:
                continue
            if source_is_denied(doc.source.value, doc.conv_id, overrides):
                doc = doc.model_copy(update={"sensitivity": Sensitivity.NDA})
            if doc.sensitivity is Sensitivity.NDA:
                stats = stats.bump(nda_docs=1)
            elif doc.sensitivity is Sensitivity.COPYRIGHT:
                # Eigene Spalte, nicht unter clean verbucht. Fremdmaterial als
                # clean zu melden wuerde genau die Zahl verfaelschen, an der
                # das NDA-Gate und der Tagesbericht gelesen werden.
                stats = stats.bump(copyright_docs=1)
            else:
                stats = stats.bump(clean_docs=1)
            if not dry_run:
                write_doc(doc, corpus_root)
            stats = stats.bump(written=1)
            wrote_any = True

        if not wrote_any:
            stats = stats.bump(skipped_empty=1)
        if not dry_run:
            mark.mark(path)

    if not dry_run:
        if expected_paths is not None and limit is None:
            removed = _remove_stale_docs(corpus_root, adapter.source, expected_paths)
            stats = stats.bump(removed_stale=removed)
        mark.save()
    return stats
