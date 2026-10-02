"""Schreiben und Lesen des Markdown-Corpus.

Der Body ist bewusst reine Prosa mit Rollen-Headern, damit ripgrep darauf
funktioniert. Die Provenance sitzt im YAML-Frontmatter, das Datum zusaetzlich im
Pfad, weil Pfad und Dateiname fuer einen Agenten das billigste Signal sind
(Anthropic, "metadata as signal").
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from obsidiyan.models import Doc, Role, Sensitivity, Source, Turn

# Der Zeitstempel muss ISO-foermig sein, sonst frisst er den maschinell-Marker.
_TURN_RE = re.compile(r"^## (user|assistant)(?: · (\d{4}-\d{2}-\d{2}\S*))?( · maschinell)?$")
_ESCAPE_PREFIX = "\\"


#

_UMLAUTS = str.maketrans(
    {"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss"}
)


def slugify(text: str, max_len: int = 48) -> str:
    """Deutsche Umlaute werden transliteriert, nicht weggeworfen.
    Sonst wird aus 'Groesse' ein 'gre' und der Dateiname verliert sein Signal."""
    norm = unicodedata.normalize("NFKD", text.translate(_UMLAUTS))
    ascii_only = norm.encode("ascii", "ignore").decode("ascii").lower()
    cleaned = re.sub(r"[^a-z0-9]+", "-", ascii_only).strip("-")
    return (cleaned[:max_len].rstrip("-")) or "untitled"


def short_id(conv_id: str, length: int = 8) -> str:
    return hashlib.sha1(conv_id.encode("utf-8")).hexdigest()[:length]


def doc_path(doc: Doc, corpus_root: Path) -> Path:
    day = doc.started_at.date().isoformat() if doc.started_at else "0000-00-00"
    year = day[:4]
    stem = f"{day}--{doc.source.value}--{slugify(doc.title or doc.project)}"
    return corpus_root / doc.source.value / year / f"{stem}--{short_id(doc.conv_id)}.md"


def _escape_body_line(line: str) -> str:
    return _ESCAPE_PREFIX + line if _TURN_RE.match(line) else line


def _unescape_body_line(line: str) -> str:
    stripped = line[len(_ESCAPE_PREFIX) :]
    return stripped if line.startswith(_ESCAPE_PREFIX) and _TURN_RE.match(stripped) else line


def render(doc: Doc) -> str:
    meta: dict[str, Any] = {
        "source": doc.source.value,
        "conv_id": doc.conv_id,
        "title": doc.title,
        "project": doc.project,
        "started_at": doc.started_at.isoformat() if doc.started_at else None,
        "ended_at": doc.ended_at.isoformat() if doc.ended_at else None,
        "sensitivity": doc.sensitivity.value,
        "cwd": doc.cwd,
        "git_branch": doc.git_branch,
        "turns": len(doc.turns),
    }
    front = yaml.safe_dump(meta, allow_unicode=True, sort_keys=True).rstrip("\n")
    parts = [f"---\n{front}\n---\n"]
    for turn in doc.turns:
        stamp = f" · {turn.ts.isoformat()}" if turn.ts else ""
        mark = " · maschinell" if turn.machine_summary else ""
        body = "\n".join(_escape_body_line(line) for line in turn.text.splitlines())
        parts.append(f"\n## {turn.role.value}{stamp}{mark}\n\n{body}\n")
    return "".join(parts)


def parse(text: str) -> Doc:
    if not text.startswith("---\n"):
        raise ValueError("kein Frontmatter gefunden")
    _, front, body = text.split("---\n", 2)
    meta = yaml.safe_load(front) or {}

    turns: list[Turn] = []
    role: Role | None = None
    ts: datetime | None = None
    machine = False
    buf: list[str] = []

    def flush() -> None:
        if role is not None:
            turns.append(
                Turn(
                    role=role,
                    ts=ts,
                    text="\n".join(buf).strip("\n"),
                    machine_summary=machine,
                )
            )

    for line in body.splitlines():
        match = _TURN_RE.match(line)
        if match:
            flush()
            role = Role(match.group(1))
            ts = datetime.fromisoformat(match.group(2)) if match.group(2) else None
            machine = bool(match.group(3))
            buf = []
        elif role is not None:
            buf.append(_unescape_body_line(line))
    flush()

    return Doc(
        source=Source(meta["source"]),
        conv_id=str(meta["conv_id"]),
        title=meta.get("title") or "",
        project=meta.get("project") or "",
        started_at=_dt(meta.get("started_at")),
        ended_at=_dt(meta.get("ended_at")),
        sensitivity=Sensitivity(meta.get("sensitivity", "nda")),
        cwd=meta.get("cwd") or "",
        git_branch=meta.get("git_branch") or "",
        turns=tuple(turns),
    )


def _dt(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def write_doc(doc: Doc, corpus_root: Path) -> Path:
    path = doc_path(doc, corpus_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(doc), encoding="utf-8")
    return path


def read_doc(path: Path) -> Doc:
    return parse(path.read_text(encoding="utf-8"))


def iter_docs(corpus_root: Path) -> Iterator[Path]:
    yield from sorted(corpus_root.rglob("*.md"))
