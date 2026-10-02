"""Integritaetsregeln fuer den kuratierten Obsidian-Graphen.

Der Corpus bleibt die Suchschicht. Dieser Graph bildet nur die bewusst
kuratierten Einstiege aus BRAIN.md, notes/ und private/ ab. Jeder Knoten ausser
BRAIN braucht genau einen Parent, dessen Kette bis BRAIN reicht.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

_WIKILINK_RE = re.compile(r"\[\[([^\[\]\n]+)\]\]")
_INLINE_CODE_RE = re.compile(r"(`+)(.*?)\1")
_REQUIRED_IGNORE_FILTERS = frozenset({"memory/", "private/memory/"})
_ALLOWED_MISSING_LINKS = frozenset({"private/INDEX"})
ALLOWED_BRAIN_CHILDREN = frozenset(
    {
        "notes/profile",
        "notes/projects",
        "notes/knowledge",
        "private/INDEX",
    }
)


@dataclass(frozen=True)
class GraphReport:
    nodes: int
    parent_edges: int
    links: int
    components: int
    errors: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.errors

    def render(self) -> str:
        status = "PASS" if self.ok else "FAIL"
        summary = (
            f"graph={status} nodes={self.nodes} parent_edges={self.parent_edges} "
            f"links={self.links} components={self.components}"
        )
        if self.ok:
            return summary
        return "\n".join([summary, *(f"- {error}" for error in self.errors)])


def _node_id(path: Path, root: Path) -> str:
    return path.relative_to(root).with_suffix("").as_posix()


def curated_files(root: Path) -> list[Path]:
    """Liefert nur den menschlich kuratierten Layer, nie Roh-Memory."""
    files: list[Path] = []
    brain = root / "BRAIN.md"
    if brain.is_file() and not brain.is_symlink():
        files.append(brain)
    notes = root / "notes"
    if notes.is_dir() and not notes.is_symlink():
        files.extend(
            path for path in notes.rglob("*.md") if path.is_file() and not path.is_symlink()
        )
    private = root / "private"
    if private.is_dir() and not private.is_symlink():
        files.extend(
            path
            for path in private.rglob("*.md")
            if path.is_file()
            and not path.is_symlink()
            and "memory" not in path.relative_to(private).parts
        )
    return sorted(set(files))


def normalize_reference(value: str) -> str | None:
    """Normalisiert einen Wikilink oder Pfad auf eine Vault-relative Node-ID."""
    reference = value.strip()
    if (reference.startswith("\"") and reference.endswith("\"")) or (
        reference.startswith("'") and reference.endswith("'")
    ):
        reference = reference[1:-1].strip()
    match = _WIKILINK_RE.fullmatch(reference)
    if match is not None:
        reference = match.group(1).strip()
    reference = reference.split("|", 1)[0].split("#", 1)[0].strip()
    if reference.endswith(".md"):
        reference = reference[:-3]
    reference = reference.replace("\\", "/").strip("/")
    if not reference or any(part in {"", ".", ".."} for part in reference.split("/")):
        return None
    return reference


def _frontmatter(text: str) -> tuple[list[str], str]:
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        return [], text
    closing = next((index for index, line in enumerate(lines[1:], start=1) if line == "---"), None)
    if closing is None:
        return [], text
    return lines[1:closing], "\n".join(lines[closing + 1 :])


def _parent_values(text: str) -> list[str]:
    frontmatter, _ = _frontmatter(text)
    return [line.split(":", 1)[1].strip() for line in frontmatter if line.startswith("parent:")]


def _visible_markdown(text: str) -> str:
    """Entfernt Frontmatter sowie Inline- und Fence-Code fuer Link-Pruefungen."""
    _, body = _frontmatter(text)
    visible: list[str] = []
    fence: str | None = None
    for line in body.splitlines():
        stripped = line.lstrip()
        marker = stripped[:3]
        if fence is None and marker in {"```", "~~~"}:
            fence = marker
            continue
        if fence is not None:
            if marker == fence:
                fence = None
            continue
        visible.append(_INLINE_CODE_RE.sub("", line))
    return "\n".join(visible)


def _markdown_links(text: str) -> list[str]:
    return [match.group(1) for match in _WIKILINK_RE.finditer(_visible_markdown(text))]


def _malformed_wikilinks(text: str) -> list[str]:
    errors: list[str] = []
    for line_number, line in enumerate(_visible_markdown(text).splitlines(), start=1):
        remainder = _WIKILINK_RE.sub("", line)
        if "[[" in remainder or "]]" in remainder:
            errors.append(
                f"unvollstaendiger oder mehrzeiliger Wikilink in Body-Zeile {line_number}"
            )
    return errors


def _resolve(
    raw: str,
    by_id: dict[str, str],
    by_stem: dict[str, list[str]],
) -> tuple[str | None, str | None]:
    reference = normalize_reference(raw)
    if reference is None:
        return None, f"ungueltige Referenz {raw!r}"
    if "/" in reference:
        target = by_id.get(reference.casefold())
        if target is None:
            return None, f"fehlendes Ziel [[{reference}]]"
        return target, None
    matches = by_stem.get(reference.casefold(), [])
    if not matches:
        return None, f"fehlendes Ziel [[{reference}]]"
    if len(matches) > 1:
        return None, f"mehrdeutiges Ziel [[{reference}]]: {', '.join(matches)}"
    return matches[0], None


def _component_count(nodes: set[str], edges: set[tuple[str, str]]) -> int:
    neighbours = {node: set[str]() for node in nodes}
    for source, target in edges:
        neighbours[source].add(target)
        neighbours[target].add(source)
    unseen = set(nodes)
    count = 0
    while unseen:
        count += 1
        stack = [unseen.pop()]
        while stack:
            current = stack.pop()
            adjacent = neighbours[current] & unseen
            unseen.difference_update(adjacent)
            stack.extend(adjacent)
    return count


def _obsidian_state(root: Path) -> tuple[set[str], list[str]]:
    config = root / ".obsidian" / "app.json"
    if not config.is_file():
        return set(), [".obsidian/app.json fehlt"]
    try:
        payload = json.loads(config.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return set(), [f".obsidian/app.json ist unlesbar: {exc}"]
    configured = payload.get("userIgnoreFilters")
    if not isinstance(configured, list) or not all(isinstance(item, str) for item in configured):
        return set(), [".obsidian/app.json: userIgnoreFilters muss eine String-Liste sein"]
    filters = set(configured)
    missing = sorted(_REQUIRED_IGNORE_FILTERS - filters)
    if not missing:
        return filters, []
    return filters, ["Obsidian zeigt Roh-Layer an; Ignore-Filter fehlen: " + ", ".join(missing)]


def _unmanaged_markdown_errors(root: Path, ignore_filters: set[str]) -> list[str]:
    """Findet sichtbare Markdown-Inseln ausserhalb der kuratierten Layer."""
    errors: list[str] = []
    for path in root.glob("*.md"):
        if path.name == "BRAIN.md":
            continue
        if path.is_symlink():
            errors.append(f"unkuratierte Root-Note darf kein Symlink sein: {path.name}")
            continue
        if not path.is_file():
            continue
        label = "leerer Root-Platzhalter" if path.stat().st_size == 0 else "unkuratierte Root-Note"
        errors.append(f"{label}: {path.name}")

    for folder in root.iterdir():
        if folder.name.startswith("."):
            continue
        if folder.name in {"notes", "private"} or f"{folder.name}/" in ignore_filters:
            continue
        if folder.is_symlink():
            errors.append(f"sichtbarer unkuratierter Ordner darf kein Symlink sein: {folder.name}")
            continue
        if not folder.is_dir():
            continue
        for path in folder.rglob("*.md"):
            relative = path.relative_to(root)
            if path.is_symlink():
                errors.append(f"sichtbare unkuratierte Markdown-Note ist Symlink: {relative}")
            elif path.is_file():
                errors.append(
                    f"sichtbare Markdown-Note ausserhalb des kuratierten Graphen: {relative}"
                )
    return errors


def audit_graph(root: Path) -> GraphReport:
    """Prueft Parent-Baum, Links, Sichtfilter und zusammenhaengende Struktur."""
    root = root.resolve()
    paths = curated_files(root)
    errors: list[str] = []
    brain = root / "BRAIN.md"
    if not brain.is_file():
        errors.append("BRAIN.md fehlt")
    elif brain.is_symlink():
        errors.append("BRAIN.md darf kein Symlink sein")
    for layer in (root / "notes", root / "private"):
        if layer.is_symlink():
            errors.append(f"{layer.name}/ darf kein Symlink sein")
        elif layer.is_dir():
            for path in layer.rglob("*"):
                if path.is_symlink():
                    node = path.relative_to(root).with_suffix("").as_posix()
                    errors.append(f"kuratierter Layer-Eintrag darf kein Symlink sein: {node}")

    nodes = {_node_id(path, root): path for path in paths}
    by_id: dict[str, str] = {}
    for node in nodes:
        folded = node.casefold()
        if folded in by_id:
            errors.append(f"doppelte Node-ID: {by_id[folded]} und {node}")
        else:
            by_id[folded] = node

    by_stem: dict[str, list[str]] = {}
    for node in nodes:
        by_stem.setdefault(Path(node).name.casefold(), []).append(node)
    for matches in by_stem.values():
        if len(matches) > 1:
            errors.append("doppelter Node-Name: " + ", ".join(sorted(matches)))

    parent_of: dict[str, str] = {}
    edges: set[tuple[str, str]] = set()
    for node, path in nodes.items():
        text = path.read_text(encoding="utf-8")
        errors.extend(f"{node}: {error}" for error in _malformed_wikilinks(text))
        values = _parent_values(text)
        if node == "BRAIN":
            if values:
                errors.append("BRAIN ist die Wurzel und darf keinen parent haben")
        elif len(values) != 1:
            errors.append(f"{node}: genau ein parent ist Pflicht, gefunden {len(values)}")
        else:
            parent, error = _resolve(values[0], by_id, by_stem)
            if error is not None:
                errors.append(f"{node}: parent {error}")
            elif parent is not None:
                if parent == node:
                    errors.append(f"{node}: parent verweist auf sich selbst")
                elif parent == "BRAIN" and node not in ALLOWED_BRAIN_CHILDREN:
                    errors.append(f"{node}: ist kein erlaubter direkter BRAIN-Child")
                elif node.startswith("notes/") and parent.startswith("private/"):
                    errors.append(f"{node}: Clean-Node darf keinen privaten parent haben")
                elif node.startswith("private/") and parent != "BRAIN" and not parent.startswith(
                    "private/"
                ):
                    errors.append(
                        f"{node}: Private-Node darf nur BRAIN oder private/ als parent haben"
                    )
                else:
                    parent_of[node] = parent
                    edges.add((node, parent))

        for raw_link in _markdown_links(text):
            target, error = _resolve(raw_link, by_id, by_stem)
            normalized = normalize_reference(raw_link)
            if error is not None:
                allow_private_stub = (
                    normalized in _ALLOWED_MISSING_LINKS and not (root / "private").exists()
                )
                if not allow_private_stub:
                    errors.append(f"{node}: {error}")
                continue
            if target is not None and target != node:
                edges.add((node, target))

    reported_cycles: set[frozenset[str]] = set()
    for start in parent_of:
        trail: list[str] = []
        current = start
        while current != "BRAIN":
            if current in trail:
                cycle = [*trail[trail.index(current) :], current]
                key = frozenset(cycle)
                if key not in reported_cycles:
                    errors.append("parent-Zyklus: " + " -> ".join(cycle))
                    reported_cycles.add(key)
                break
            trail.append(current)
            parent = parent_of.get(current)
            if parent is None:
                if current != start or start in parent_of:
                    errors.append(f"{start}: parent-Kette erreicht BRAIN nicht")
                break
            current = parent

    ignore_filters, obsidian_errors = _obsidian_state(root)
    errors.extend(obsidian_errors)
    errors.extend(_unmanaged_markdown_errors(root, ignore_filters))
    components = _component_count(set(nodes), edges) if nodes else 0
    if nodes and not errors and components != 1:
        errors.append(f"kuratierter Graph hat {components} Komponenten statt 1")
    return GraphReport(
        nodes=len(nodes),
        parent_edges=len(parent_of),
        links=len(edges),
        components=components,
        errors=tuple(dict.fromkeys(errors)),
    )
