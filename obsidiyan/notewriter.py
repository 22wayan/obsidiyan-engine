"""Schreiben in die kuratierten Schichten notes/ und private/.

Der Gegenpart zum Lesen: ein Agent soll Erkenntnisse ablegen koennen, nicht nur
abrufen. Allgemeines Wissen landet in notes/, NDA- und Kundenwissen im lokal
gitignorierten private/-Layer.

Drei harte Regeln:
- NDA-Material wird in notes/ abgelehnt. Fuer private/ gibt es einen eigenen,
  expliziten Schreibpfad mit erzwungener NDA-Klassifikation.
- Bestehende Notes werden ergaenzt, nie ueberschrieben. Das ist die
  Schreibregel aus BRAIN.md, hier mechanisch statt als Bitte.
- Neue Notes brauchen einen existierenden Parent im selben kuratierten Layer.
  Dadurch kann kein Agent versehentlich eine neue Graph-Insel erzeugen.
"""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from obsidiyan.corpusio import slugify
from obsidiyan.graph import ALLOWED_BRAIN_CHILDREN, normalize_reference
from obsidiyan.models import Sensitivity
from obsidiyan.nda import classify, text_has_email, text_has_secret

_MODIFIED_RE = re.compile(r"^modified:\s*\d{4}-\d{2}-\d{2}\s*$", re.MULTILINE)
_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---(?=\n|\Z)", re.DOTALL)


@dataclass(frozen=True)
class WriteResult:
    path: Path
    created: bool
    appended: bool
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


def find_in_layer(slug: str, layer_root: Path) -> Path | None:
    """Sucht eine vorhandene Note im ganzen Layer, nicht nur an einer Stelle.

    Der Layer kennt zwei Ablageregeln, die sich sonst widersprechen:
    ``remember_private`` ordnet neue Notes nach Mandat in Ordner, ``emit`` legt
    sie neben ihren Parent. Solange beide nur ihre eigene Regel anwenden, legt
    die eine Seite eine zweite Datei an, wo die andere schon eine hat. Genau so
    sind am 2026-09-16 zwei ``teamco-website-inhalt-und-stack.md`` entstanden und
    haben jeden Wikilink darauf mehrdeutig gemacht.

    Eine vorhandene Note gewinnt deshalb gegen die Ablageregel. Liegt der Name
    mehrfach, ist Raten die schlechtere Antwort als ein Fehler.

    ``memory`` bleibt aussen vor: der Ordner wird automatisch befuellt und nie
    kuratiert.
    """
    matches = [
        path
        for path in sorted(layer_root.rglob(f"{slug}.md"))
        if path.is_file()
        and not path.is_symlink()
        and "memory" not in path.relative_to(layer_root).parts
    ]
    if len(matches) > 1:
        doppelt = ", ".join(str(m) for m in matches)
        raise ValueError(f"{slug}.md liegt mehrfach im Layer: {doppelt}")
    return matches[0] if matches else None


def _one_line(value: str) -> str:
    """User-Felder duerfen weder YAML-Frontmatter noch Markdown-Headings aufbrechen."""
    return " ".join(value.split())


def _yaml_scalar(value: str) -> str:
    """Ein einfacher, YAML-kompatibler quoted scalar ohne weitere Abhaengigkeit."""
    return "'" + _one_line(value).replace("'", "''") + "'"


def is_safe_target(path: Path, root: Path) -> bool:
    """Auch bestehende Symlinks duerfen einen Writer nicht aus seinem Layer fuehren."""
    try:
        if root.is_symlink():
            return False
        relative = path.relative_to(root)
        current = root
        for part in relative.parts:
            current /= part
            if current.is_symlink():
                return False
        resolved_root = root.resolve()
        resolved_path = path.resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return False
    return resolved_path == resolved_root or resolved_root in resolved_path.parents


def atomic_write_text(path: Path, content: str) -> None:
    """Ersetzt genau den Ziel-Eintrag, ohne Symlinks oder Hardlinks zu folgen."""
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _update_modified(existing: str, stamp: date) -> str:
    """Aendert modified nur im ersten, vollstaendig geschlossenen Frontmatter."""
    match = _FRONTMATTER_RE.match(existing)
    if match is None:
        return existing
    front = match.group(1)
    replacement = f"modified: {stamp.isoformat()}"
    if _MODIFIED_RE.search(front):
        front = _MODIFIED_RE.sub(replacement, front, count=1)
    else:
        front = f"{front}\n{replacement}"
    return f"---\n{front}\n---{existing[match.end() :]}"


def _frontmatter(
    title: str,
    today: date,
    source: str,
    *,
    sensitivity: Sensitivity | None = None,
    project: str = "",
    parent: str = "",
) -> str:
    lines = [
        "---",
        f"created: {today.isoformat()}",
        f"modified: {today.isoformat()}",
        "ai_generated: true",
        f"source: {_yaml_scalar(source)}",
    ]
    if parent:
        escaped_parent = parent.replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'parent: "[[{escaped_parent}]]"')
    if sensitivity is not None:
        lines.append(f"sensitivity: {sensitivity.value}")
    if project:
        lines.append(f"project: {_yaml_scalar(project)}")
    return "\n".join([*lines, "---", "", f"# {title}", ""])


def _write(
    path: Path,
    title: str,
    body: str,
    *,
    section: str,
    source: str,
    stamp: date,
    sensitivity: Sensitivity | None = None,
    project: str = "",
    parent: str = "",
) -> WriteResult:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        front = _frontmatter(
            title,
            stamp,
            source,
            sensitivity=sensitivity,
            project=project,
            parent=parent,
        )
        atomic_write_text(path, front + body + "\n")
        return WriteResult(path, True, False)

    existing = path.read_text(encoding="utf-8")
    if "distilled_from:" in existing[:400]:
        return WriteResult(
            path,
            False,
            False,
            error=(
                f"abgelehnt: {path.name} ist eine destillierte Note und wird beim "
                "naechsten Emit vollstaendig aus dem Claim-Store neu geschrieben. "
                "Ein Nachtrag hier waere danach still verschwunden. Leg die "
                "Erkenntnis stattdessen als Claim ab (add-claims mit Quelle aus "
                "dem Corpus) oder haeng sie an die handgeschriebene Nabe darueber."
            ),
        )
    heading = _one_line(section) or f"Nachtrag {stamp.isoformat()}"
    addition = f"\n\n## {heading}\n\n{body}\n"
    updated = _update_modified(existing, stamp)
    atomic_write_text(path, updated.rstrip("\n") + addition)
    return WriteResult(path, False, True)


def _vault_root(layer_root: Path) -> Path:
    return layer_root.parent if layer_root.name in {"notes", "private"} else layer_root


def parent_assignment_error(path: Path, layer_root: Path, parent: str) -> str:
    """Schuetzt die wenigen absichtlich direkten BRAIN-Einstiege."""
    if parent != "BRAIN" or layer_root.name not in {"notes", "private"}:
        return ""
    node = path.relative_to(_vault_root(layer_root)).with_suffix("").as_posix()
    if node in ALLOWED_BRAIN_CHILDREN:
        return ""
    return f"abgelehnt: [[{node}]] ist kein erlaubter direkter BRAIN-Child"


def resolve_parent(
    value: str,
    layer_root: Path,
    *,
    private: bool,
) -> tuple[str, str]:
    """Loest einen Parent eindeutig auf und erzwingt die Layer-Grenze."""
    reference = normalize_reference(_one_line(value))
    if reference is None:
        return "", "abgelehnt: neue Note braucht einen gueltigen bestehenden parent"

    vault_root = _vault_root(layer_root)
    brain = vault_root / "BRAIN.md"
    candidates: list[Path]
    if "/" in reference:
        candidates = [vault_root / f"{reference}.md"]
    else:
        candidates = []
        if reference.casefold() == "brain":
            candidates.append(brain)
        if layer_root.is_dir() and not layer_root.is_symlink():
            candidates.extend(
                path
                for path in layer_root.rglob("*.md")
                if path.stem.casefold() == reference.casefold()
                and (not private or "memory" not in path.relative_to(layer_root).parts)
            )
    candidates = list(dict.fromkeys(candidates))
    existing = [path for path in candidates if path.is_file() and not path.is_symlink()]
    if not existing:
        return "", f"abgelehnt: parent [[{reference}]] existiert nicht"
    if len(existing) > 1:
        matches = ", ".join(str(path) for path in existing)
        return "", f"abgelehnt: parent [[{reference}]] ist mehrdeutig: {matches}"

    target = existing[0]
    if target == brain:
        if brain.is_symlink():
            return "", "abgelehnt: BRAIN parent darf kein Symlink sein"
        return "BRAIN", ""
    if not is_safe_target(target, layer_root):
        return "", "abgelehnt: parent verlaesst den erlaubten Layer"
    if not private and "private" in target.relative_to(vault_root).parts:
        return "", "abgelehnt: Clean-Note darf keinen privaten parent haben"
    if private and "memory" in target.relative_to(layer_root).parts:
        return "", "abgelehnt: private/memory ist Rohmaterial und kein parent"
    canonical = target.relative_to(vault_root).with_suffix("").as_posix()
    return canonical, ""


def _validate_existing_parent(
    path: Path,
    layer_root: Path,
    *,
    private: bool,
    requested: str,
) -> str:
    text = path.read_text(encoding="utf-8")
    match = _FRONTMATTER_RE.match(text)
    if match is None:
        return "abgelehnt: bestehende Note hat kein vollstaendiges Frontmatter mit parent"
    values = [
        line.split(":", 1)[1].strip()
        for line in match.group(1).splitlines()
        if line.startswith("parent:")
    ]
    if len(values) != 1:
        return f"abgelehnt: bestehende Note braucht genau einen parent, gefunden {len(values)}"
    stored, error = resolve_parent(values[0], layer_root, private=private)
    if error:
        return f"abgelehnt: bestehender parent ist ungueltig: {error}"
    current = path.relative_to(_vault_root(layer_root)).with_suffix("").as_posix()
    if stored.casefold() == current.casefold():
        return "abgelehnt: bestehende Note verweist auf sich selbst als parent"
    assignment_error = parent_assignment_error(path, layer_root, stored)
    if assignment_error:
        return assignment_error
    if requested:
        supplied, error = resolve_parent(requested, layer_root, private=private)
        if error:
            return error
        if supplied != stored:
            return f"abgelehnt: parent-Konflikt, gespeichert [[{stored}]], angefragt [[{supplied}]]"
    return ""


def remember(
    title: str,
    body: str,
    notes_root: Path,
    *,
    section: str = "",
    parent: str = "",
    source: str = "Agent-Eintrag ueber MCP",
    today: date | None = None,
) -> WriteResult:
    """Legt eine Note an oder haengt einen Abschnitt an eine bestehende an."""
    title = _one_line(title)
    body = body.strip()
    if not title or not body:
        return WriteResult(notes_root, False, False, "title und body duerfen nicht leer sein")

    checked_text = f"{title}\n{body}\n{section}\n{source}\n{parent}"
    if text_has_email(checked_text):
        return WriteResult(
            notes_root,
            False,
            False,
            "abgelehnt: E-Mail-Adressen gehoeren nicht in den git-getrackten Clean-Layer.",
        )
    if text_has_secret(checked_text):
        return WriteResult(notes_root, False, False, "abgelehnt: moegliches Secret erkannt")
    if classify("notes", checked_text) is Sensitivity.NDA:
        return WriteResult(
            notes_root,
            False,
            False,
            "abgelehnt: Text enthaelt NDA-Entities. notes/ ist git-getrackt, "
            "NDA-Material gehoert dort nie hinein.",
        )

    stamp = today or date.today()
    path = notes_root / f"{slugify(title, max_len=60)}.md"
    if not is_safe_target(path, notes_root):
        return WriteResult(notes_root, False, False, "abgelehnt: Ziel verlaesst notes_root")
    parent_ref = ""
    if path.exists():
        error = _validate_existing_parent(
            path,
            notes_root,
            private=False,
            requested=parent,
        )
        if error:
            return WriteResult(notes_root, False, False, error)
    else:
        parent_ref, error = resolve_parent(parent, notes_root, private=False)
        if error:
            return WriteResult(notes_root, False, False, error)
        error = parent_assignment_error(path, notes_root, parent_ref)
        if error:
            return WriteResult(notes_root, False, False, error)
    return _write(
        path,
        title,
        body,
        section=section,
        source=source,
        stamp=stamp,
        parent=parent_ref,
    )


def remember_private(
    project: str,
    title: str,
    body: str,
    private_root: Path,
    *,
    section: str = "",
    parent: str = "",
    source: str = "Privater Agent-Eintrag ueber MCP",
    today: date | None = None,
) -> WriteResult:
    """Legt NDA-Wissen im lokalen, gitignorierten private/-Layer ab."""
    project = _one_line(project)
    title = _one_line(title)
    body = body.strip()
    if not project or not title or not body:
        return WriteResult(
            private_root,
            False,
            False,
            "project, title und body duerfen nicht leer sein",
        )
    if text_has_secret(f"{project}\n{title}\n{body}\n{section}\n{source}\n{parent}"):
        return WriteResult(private_root, False, False, "abgelehnt: moegliches Secret erkannt")

    stamp = today or date.today()
    slug = slugify(title, max_len=60)
    try:
        vorhanden = find_in_layer(slug, private_root)
    except ValueError as exc:
        return WriteResult(private_root, False, False, f"abgelehnt: {exc}")
    path = vorhanden or (private_root / slugify(project, max_len=60) / f"{slug}.md")
    if not is_safe_target(path, private_root):
        return WriteResult(private_root, False, False, "abgelehnt: Ziel verlaesst private_root")
    parent_ref = ""
    if path.exists():
        error = _validate_existing_parent(
            path,
            private_root,
            private=True,
            requested=parent,
        )
        if error:
            return WriteResult(private_root, False, False, error)
    else:
        parent_ref, error = resolve_parent(parent, private_root, private=True)
        if error:
            return WriteResult(private_root, False, False, error)
        error = parent_assignment_error(path, private_root, parent_ref)
        if error:
            return WriteResult(private_root, False, False, error)
    return _write(
        path,
        title,
        body,
        section=section,
        source=source,
        stamp=stamp,
        sensitivity=Sensitivity.NDA,
        project=project,
        parent=parent_ref,
    )
