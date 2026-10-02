"""Ingest fuer Claude-Code-Session-Transkripte (~/.claude/projects/**/*.jsonl).

Destillation statt Kopie. Behalten wird, was ein Mensch geschrieben oder gelesen
hat; alles, was nur Maschinenverkehr war, fliegt raus:

  behalten:  user-Turns mit echtem Text, assistant-Textbloecke
  verworfen: tool_use, tool_result, thinking, isMeta, isSidechain,
             attachment, file-history-snapshot, queue-operation

Die Schemafelder stammen aus einer Inspektion echter Transkripte (08.08.2026),
nicht aus Doku.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Iterator
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from obsidiyan.models import Doc, Role, Source, Turn
from obsidiyan.nda import classify
from obsidiyan.sources.harness_noise import is_machine_authored, strip_noise

PROJECTS_ROOT = Path.home() / ".claude" / "projects"
_SLUG_PREFIX = os.environ.get(
    "OBSIDIYAN_SLUG_PREFIX", "-" + str(Path.home()).strip("/").replace("/", "-") + "-"
)



def project_slug(session_path: Path) -> str:
    raw = session_path.parent.name
    if raw == _SLUG_PREFIX.rstrip("-"):
        return "_home"
    return raw.removeprefix(_SLUG_PREFIX) or "_home"


@lru_cache(maxsize=256)
def origin_repo(cwd: str) -> str:
    """Name des Haupt-Repos zu einem Arbeitsverzeichnis, oder leer.

    Ein Worktree traegt den Repo-Namen nicht zwingend im Pfad. Liegt er
    ausserhalb des Repos und heisst neutral, faellt der Name aus dem
    Projekt-Slug heraus, und die pfadbasierte NDA-Regel greift nicht mehr.
    `--git-common-dir` zeigt auch aus einem Worktree heraus auf das .git des
    Haupt-Repos; dessen Elternverzeichnis ist der gesuchte Name.

    Faellt still auf leer zurueck, wenn das Verzeichnis kein Repo (mehr) ist.
    Der Aufrufer nutzt den Wert nur zusaetzlich, nie ersetzend.
    """
    if not cwd or not Path(cwd).is_dir():
        return ""
    try:
        proc = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if proc.returncode != 0:
        return ""
    common = proc.stdout.strip()
    if not common:
        return ""
    return Path(common).parent.name


# Die Destillations-Automatik ruft Claude Code headless auf. Diese Laeufe
# erzeugen ganz normale Session-Transkripte und wuerden sonst selbst zu
# Destillations-Kandidaten: die Automatik fuettert sich sonst endlos selbst.
# Die Prompts unter scripts/ tragen deshalb diesen Marker in der ersten Zeile.
AUTOMATION_MARKER = "obsidiyan-automation"


def is_automation_run(turns: list[Turn]) -> bool:
    """True, wenn die Sitzung ein maschineller Lauf der eigenen Automatik ist.

    Geprueft wird nur der erste Nutzer-Turn: dort steht der uebergebene Prompt.
    Spaeter im Verlauf zitierter Markertext soll keine echte Sitzung ausschliessen.
    """
    for turn in turns:
        if turn.role is Role.USER:
            return AUTOMATION_MARKER in turn.text
    return False


def _ts(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _user_text(message: dict[str, Any]) -> str:
    """Nur echter Nutzer-Text. tool_result-Bloecke sind Maschinenverkehr."""
    content = message.get("content")
    if isinstance(content, str):
        return strip_noise(content)
    if not isinstance(content, list):
        return ""
    parts = [
        b.get("text", "")
        for b in content
        if isinstance(b, dict) and b.get("type") == "text" and b.get("text")
    ]
    return strip_noise("\n".join(parts))


def _assistant_text(message: dict[str, Any]) -> str:
    """Nur sichtbarer Antworttext. thinking und tool_use gehoeren nicht in den Corpus."""
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts = [
        b.get("text", "")
        for b in content
        if isinstance(b, dict) and b.get("type") == "text" and b.get("text")
    ]
    return "\n".join(parts).strip()


def parse_session(path: Path) -> Doc | None:
    """Ein JSONL-Transkript zu einem Doc destillieren. None, wenn nichts uebrig bleibt."""
    turns: list[Turn] = []
    title = ""
    cwd = ""
    branch = ""
    conv_id = path.stem

    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue  # abgeschnittene letzte Zeile einer laufenden Session
            if not isinstance(obj, dict):
                continue

            kind = obj.get("type")
            if kind == "ai-title":
                title = str(obj.get("aiTitle") or "").strip() or title
                continue
            if kind not in ("user", "assistant"):
                continue
            if obj.get("isMeta") or obj.get("isSidechain"):
                continue

            cwd = cwd or str(obj.get("cwd") or "")
            branch = branch or str(obj.get("gitBranch") or "")
            conv_id = str(obj.get("sessionId") or conv_id)

            message = obj.get("message")
            if not isinstance(message, dict):
                continue

            if kind == "user":
                text, role = _user_text(message), Role.USER
            else:
                text, role = _assistant_text(message), Role.ASSISTANT
            if not text:
                continue
            machine = role is Role.USER and is_machine_authored(text)
            turns.append(
                Turn(role=role, ts=_ts(obj.get("timestamp")), text=text, machine_summary=machine)
            )

    if not turns:
        return None
    if is_automation_run(turns):
        return None

    slug = project_slug(path)
    origin = origin_repo(cwd)
    # Die Zuordnung folgt dem Haupt-Repo, nicht dem Verzeichnisnamen. Sonst
    # erscheint jeder Worktree als eigenes Projekt, und genau das wird zur
    # Regel, sobald Sessions aus einem Workspace-Manager kommen: ein realer
    # Workspace lag hier bereits als "conductor-workspaces-nebenprojekt-tripoli"
    # statt als "nebenprojekt" im Corpus. Laesst sich das Repo nicht aufloesen,
    # gilt weiterhin der Verzeichnis-Slug.
    project = origin or slug
    # Die Klassifikation sieht bewusst weiterhin BEIDE Namen. Sie darf durch
    # eine Umbenennung nichts verlieren, auch wenn eine Deny-Regel auf der
    # alten Schreibweise steht.
    slug_for_classify = f"{slug} {origin}" if origin and origin != slug else slug
    stamps = [t.ts for t in turns if t.ts]
    doc = Doc(
        source=Source.CLAUDE_CODE,
        conv_id=conv_id,
        title=title,
        project=project,
        started_at=min(stamps) if stamps else None,
        ended_at=max(stamps) if stamps else None,
        sensitivity=classify(slug_for_classify, "\n".join(t.text for t in turns)),
        cwd=cwd,
        git_branch=branch,
        turns=tuple(turns),
    )
    return doc


def iter_sessions(root: Path | None = None) -> Iterator[Path]:
    """Nur echte Sessions, also direkte Kinder eines Projektordners.

    Tiefer liegen ausschliesslich Subagent-Transkripte
    (<slug>/<session>/subagents/... und .../workflows/...). Die enthalten keinen
    menschlichen Turn, sondern Maschine-zu-Maschine-Verkehr, und wuerden den
    Corpus um Groessenordnungen aufblaehen ohne Wissen hinzuzufuegen.
    """
    base = root or PROJECTS_ROOT
    if not base.exists():
        return
    yield from sorted(base.glob("*/*.jsonl"))
