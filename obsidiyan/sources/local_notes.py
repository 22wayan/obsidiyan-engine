"""Ingest fuer die kuratierten Schichten: notes/, memory/ und private/.

Alles ist schon Markdown, wird aber trotzdem in den Corpus gespiegelt, damit EINE
Suche alles abdeckt. Der Corpus ist gitignored und abgeleitet, die Duplikation
kostet also nichts und erspart dem Agenten zwei Suchpfade.

Kuratierte Notes zaehlen als Nutzer-Aussage, nicht als Assistant-Text: sie sind
redigiert und freigegeben, tragen also die Autoritaet, die das Ranking user-Turns
zuschreibt.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from obsidiyan.models import Doc, Role, Sensitivity, Source, Turn
from obsidiyan.nda import classify

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
_DATE_RE = re.compile(r"^(created|modified):\s*(\d{4}-\d{2}-\d{2})", re.MULTILINE)
_PROJECT_RE = re.compile(r"^project:\s*['\"]?(.+?)['\"]?\s*$", re.MULTILINE)
_SENSITIVITY_RE = re.compile(r"^sensitivity:\s*['\"]?(.+?)['\"]?\s*$", re.MULTILINE)
_H1_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)


def _dates(front: str, path: Path) -> tuple[datetime | None, datetime | None]:
    """Datum aus dem Frontmatter, sonst aus der Dateizeit.

    Viele Auto-Memory-Snapshots tragen kein created/modified. Ohne
    Fallback landen sie mit Datum 0000-00-00 im Corpus, und die datumsbasierte
    Konfliktaufloesung ist fuer genau das reichhaltigste Material blind.
    Die mtime ist ungenau, aber ungenau schlaegt gar nichts.
    """
    found = {key: value for key, value in _DATE_RE.findall(front)}
    created = found.get("created")
    modified = found.get("modified") or created
    if created or modified:
        return (
            datetime.fromisoformat(created) if created else None,
            datetime.fromisoformat(modified) if modified else None,
        )
    stamp = datetime.fromtimestamp(path.stat().st_mtime)
    return stamp, stamp


def parse_note(path: Path, repo_root: Path) -> Doc | None:
    raw = path.read_text(encoding="utf-8", errors="replace")
    if not raw.strip():
        return None

    match = _FRONTMATTER_RE.match(raw)
    front = match.group(1) if match else ""
    body = raw[match.end() :] if match else raw
    started, ended = _dates(front, path)

    title_match = _H1_RE.search(body)
    title = title_match.group(1).strip() if title_match else path.stem

    rel = path.relative_to(repo_root)
    front_project = _PROJECT_RE.search(front)
    if front_project:
        project = front_project.group(1).strip().replace("''", "'")
    elif rel.parts[0] == "memory" and len(rel.parts) > 2:
        project = rel.parts[1]
    elif rel.parts[:2] == ("private", "memory") and len(rel.parts) > 3:
        project = rel.parts[2]
    else:
        project = rel.parts[0]

    declared_sensitivity = _SENSITIVITY_RE.search(front)
    explicitly_nda = bool(
        declared_sensitivity and declared_sensitivity.group(1).strip().lower() == "nda"
    )
    sensitivity = (
        Sensitivity.NDA
        if rel.parts[0] == "private" or explicitly_nda
        else classify(project, body)
    )

    return Doc(
        source=Source.MEMORY,
        conv_id=str(rel),
        title=title,
        project=project,
        started_at=started,
        ended_at=ended,
        sensitivity=sensitivity,
        cwd=str(rel.parent),
        turns=(Turn(role=Role.USER, ts=ended, text=body.strip()),),
    )


def iter_notes(repo_root: Path) -> Iterator[Path]:
    for folder in ("notes", "memory", "private"):
        base = repo_root / folder
        if base.exists():
            yield from sorted(base.rglob("*.md"))
