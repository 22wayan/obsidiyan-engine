"""Ingest fuer den Kurs-Roh-Layer.

`scripts/ingest-course.sh` spiegelt einen Videokurs nach `courses/<slug>/`: pro
Lektion ein Markdown-Transkript mit Frontmatter, daneben kopierte Assets. Ohne
diesen Adapter liegen die Transkripte auf der Platte und sind fuer `search`
unsichtbar. Genau durchsuchbar zu sein war aber der einzige Grund, sie zu
erzeugen.

Zwei Eigenschaften unterscheiden Kurse von jeder anderen Quelle:

1. **Es gibt keinen Nutzer-Turn.** Ein Vortrag ist Fremdtext, kein Gespraech.
   Der Inhalt kommt deshalb als ASSISTANT-Turn in den Doc. Die
   Kandidatenauswahl wertet ausschliesslich Nutzertext und sieht Kursmaterial
   dadurch strukturell nie, unabhaengig von jedem Filter.
2. **Das Material ist urheberrechtlich geschuetzt.** Es soll auffindbar sein,
   aber niemals destilliert, laenger zitiert oder committed werden. Dafuer
   traegt es `Sensitivity.COPYRIGHT`.

Die Einstufung entscheidet der Layer, nicht das Frontmatter. Alles unter
`courses/` ist `copyright`, so wie alles unter `private/` `nda` ist. Ein
vergessenes oder falsch geschriebenes Frontmatter-Feld darf diese Grenze nicht
oeffnen.

Nur `.md` wird ingestiert. Notebooks, Skripte und PDFs bleiben Dateien: sie
sind kein Fliesstext, und ein Agent mit Dateizugriff greppt sie ohnehin direkt.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from obsidiyan.models import Doc, Role, Sensitivity, Source, Turn

COURSES_DIR = "courses"

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
_FIELD_RE = re.compile(r"^(course|module|lesson):\s*['\"]?(.+?)['\"]?\s*$", re.MULTILINE)
_H1_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)


def iter_courses(vault_root: Path) -> Iterator[Path]:
    base = vault_root / COURSES_DIR
    if base.exists():
        yield from sorted(base.rglob("*.md"))


def parse_course(path: Path, vault_root: Path) -> Doc | None:
    raw = path.read_text(encoding="utf-8", errors="replace")
    if not raw.strip():
        return None

    match = _FRONTMATTER_RE.match(raw)
    front = match.group(1) if match else ""
    body = raw[match.end() :] if match else raw
    if not body.strip():
        return None

    fields = dict(_FIELD_RE.findall(front))
    rel = path.relative_to(vault_root)

    # Der Kurs-Slug ist das Projekt. Frontmatter gewinnt, weil ein Kurs
    # umbenannt werden kann, ohne dass der Ordner mitwandert.
    project = fields.get("course") or (rel.parts[1] if len(rel.parts) > 1 else COURSES_DIR)

    h1 = _H1_RE.search(body)
    title = fields.get("lesson") or (h1.group(1).strip() if h1 else path.stem)
    if module := fields.get("module"):
        # Der Modulname traegt bei generischen Lektionstiteln ("Part 2") die
        # eigentliche Information. Ohne ihn sind Treffer nicht auseinanderzuhalten.
        title = f"{module.rsplit('/', 1)[-1]}: {title}"

    stamp = datetime.fromtimestamp(path.stat().st_mtime)

    return Doc(
        source=Source.COURSE,
        conv_id=str(rel),
        title=title,
        project=project,
        started_at=stamp,
        ended_at=stamp,
        sensitivity=Sensitivity.COPYRIGHT,
        cwd=str(rel.parent),
        turns=(Turn(role=Role.ASSISTANT, ts=stamp, text=body.strip()),),
    )
