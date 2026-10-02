"""Integritaetspruefung fuer versionierte Destillations-Provenienz."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from obsidiyan.corpusio import read_doc
from obsidiyan.models import Sensitivity


def _has_symlink_component(root: Path, relative: Path) -> bool:
    current = root
    if current.is_symlink():
        return True
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            return True
    return False


def doc_signature(doc_id: str) -> tuple[str, str, str] | None:
    """Der datumsfreie Teil einer doc_id: (quelle, slug, kurz-id).

    Der Dateiname ist ``<datum>--<quelle>--<slug>--<kurz-id>.md``. Alles ausser
    dem Datum ist stabil, das Datum wandert bei Memory-Snapshots mit der
    Dateizeit. Wer zwei doc_ids auf dieselbe Quelle pruefen will, vergleicht
    deshalb die Signatur und nicht den Pfad.
    """
    parts = Path(doc_id).stem.split("--")
    if len(parts) < 4:
        return None
    return parts[1], "--".join(parts[2:-1]), parts[-1]


def resolve_doc_id(doc_id: str, corpus_root: Path) -> Path | None:
    """Corpus-Datei zu einer doc_id, auch wenn ihr Pfad gewandert ist.

    Der Dateiname eines Corpus-Dokuments ist
    ``<datum>--<quelle>--<slug>--<kurz-id>.md``. Datum und Slug leiten sich aus
    dem Dokument ab, die Kurz-ID aus der stabilen ``conv_id``.

    Bei Memory-Snapshots ohne created/modified im Frontmatter faellt das Datum
    auf die Dateizeit zurueck (siehe sources/local_notes). ``bin/sync-memory.sh``
    schreibt diese Dateien bei jedem Refresh neu, damit springt die mtime auf
    heute, der Corpus-Pfad wandert mit, und das alte Dokument wird als veraltet
    entfernt. Jeder Claim, der darauf zeigte, haette dann keine Quelle mehr.

    Beobachtet am 2026-08-28: 122 Claims und 9 Reviews verloren so ihre Quelle,
    obwohl kein einziges Dokument wirklich fehlte. Nur das Datum im Pfad hatte
    sich verschoben. Deshalb wird bei einem Fehlschlag ueber die Kurz-ID
    nachgeschlagen, und zwar nur dann, wenn Quelle und Slug uebereinstimmen und
    genau ein Kandidat passt.
    """
    exact = corpus_root / doc_id
    if exact.is_file():
        return exact

    parts = Path(doc_id).stem.split("--")
    if len(parts) < 4:
        return None
    kurz_id = parts[-1]
    signatur = (parts[1], "--".join(parts[2:-1]))

    treffer = [
        path
        for path in corpus_root.rglob(f"*--{kurz_id}.md")
        if len(path.stem.split("--")) >= 4
        and (path.stem.split("--")[1], "--".join(path.stem.split("--")[2:-1])) == signatur
    ]
    return treffer[0] if len(treffer) == 1 else None


def distill_provenance_errors(
    corpus_root: Path,
    stores: Mapping[Path, str],
    *,
    require_clean: bool = False,
) -> list[str]:
    """Meldet fehlende, unsichere oder urheberrechtlich geschuetzte Claim-Quellen.

    ``stores`` ordnet jeder JSON-Datei das Feld mit dem relativen Corpus-Pfad
    zu, also beispielsweise ``source_doc_id`` oder ``doc_id``.
    """
    errors: list[str] = []
    references: set[tuple[str, str]] = set()

    for store_path, field in stores.items():
        if not store_path.is_file():
            errors.append(f"Provenienz-Store fehlt: {store_path.name}")
            continue
        try:
            rows = json.loads(store_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"Provenienz-Store unlesbar: {store_path.name}: {exc}")
            continue
        if not isinstance(rows, list):
            errors.append(f"Provenienz-Store ist keine Liste: {store_path.name}")
            continue
        for index, row in enumerate(rows):
            if not isinstance(row, dict) or not isinstance(row.get(field), str):
                errors.append(f"{store_path.name}[{index}] braucht {field}")
                continue
            references.add((store_path.name, row[field]))

    root = corpus_root.resolve()
    for store_name, doc_id in sorted(references):
        relative = Path(doc_id)
        if relative.is_absolute() or ".." in relative.parts:
            errors.append(f"{store_name}: ungueltiger Corpus-Pfad {doc_id!r}")
            continue
        candidate = resolve_doc_id(doc_id, corpus_root)
        if candidate is None:
            candidate = corpus_root / relative
        if _has_symlink_component(corpus_root, relative):
            errors.append(f"{store_name}: Symlink-Provenienz {doc_id!r}")
            continue
        try:
            candidate.resolve(strict=False).relative_to(root)
        except ValueError:
            errors.append(f"{store_name}: Corpus-Pfad verlaesst Root {doc_id!r}")
            continue
        if not candidate.is_file():
            errors.append(f"{store_name}: Corpus-Quelle fehlt {doc_id!r}")
            continue
        try:
            doc = read_doc(candidate)
        except (OSError, ValueError, KeyError) as exc:
            errors.append(f"{store_name}: Corpus-Quelle unlesbar {doc_id!r}: {exc}")
            continue
        if require_clean and doc.sensitivity is not Sensitivity.CLEAN:
            # Gilt nur fuer die git-getrackten Stores. Ein Claim aus einer
            # NDA-Quelle gehoert in den gitignorierten Zwilling, sonst wandert
            # vertraulicher Text ueber den Auto-Distill-PR nach GitHub.
            errors.append(
                f"{store_name}: Quelle ist {doc.sensitivity.value}, "
                f"gehoert in den privaten Store {doc_id!r}"
            )
            continue
        if doc.sensitivity is Sensitivity.COPYRIGHT:
            # NDA ist seit dem 24.08.2026 eine zulaessige Claim-Quelle; das
            # Destillat geht dann nach private/ statt nach notes/, siehe
            # obsidiyan.distill.route. Urheberrechtlich geschuetztes
            # Fremdmaterial bleibt als Quelle verboten: es ist nicht das Wissen des Nutzers
            # Wissen und darf auch als Zusammenfassung nirgendwo stehen.
            errors.append(f"{store_name}: Corpus-Quelle ist urheberrechtlich geschuetzt {doc_id!r}")

    return errors
