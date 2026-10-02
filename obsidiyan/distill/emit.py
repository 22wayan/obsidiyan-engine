"""Claims zu Notes machen und im passenden Layer ablegen.

Trennt bewusst vom Schreiben einzelner Notes (notewriter): hier kommen viele
Claims aus vielen Quellen an und werden pro Thema zu genau einer Note verdichtet.
Handgeschriebener Inhalt bleibt unveraendert; ein klar markierter
Destillat-Abschnitt wird idempotent aktualisiert.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from obsidiyan.corpusio import slugify
from obsidiyan.distill.models import Claim
from obsidiyan.distill.synthesize import render_note, resolve
from obsidiyan.graph import normalize_reference
from obsidiyan.notewriter import (
    atomic_write_text,
    find_in_layer,
    is_safe_target,
    parent_assignment_error,
    resolve_parent,
)

_DISTILL_START = "<!-- obsidiyan:distill:start -->"
_DISTILL_END = "<!-- obsidiyan:distill:end -->"


def _vault_root(layer_root: Path) -> Path:
    return layer_root.parent if layer_root.name in {"notes", "private"} else layer_root


def _note_reference(path: Path, notes_root: Path) -> str:
    return path.relative_to(_vault_root(notes_root)).with_suffix("").as_posix()


def _find_existing(slug: str, layer_root: Path) -> Path | None:
    """Sucht eine vorhandene Note im ganzen Layer, nicht nur in der Wurzel.

    Ohne das legt ein Emit neben `private/acme/docparse.md` eine zweite
    `private/docparse.md` an, statt die vorhandene fortzuschreiben. Genau so
    entstehen die Dubletten, die eine Wissensbasis unbrauchbar machen.

    Die Logik liegt in `notewriter`, damit `remember_private` dieselbe Suche
    benutzt. Zwei eigene Implementierungen waren der Grund, warum die beiden
    Schreibwege sich gegenseitig Dubletten angelegt haben.
    """
    return find_in_layer(slug, layer_root)


def _dir_beside_parent(parent_ref: str, layer_root: Path) -> Path:
    """Eine neue Note liegt neben ihrem Parent.

    In `notes/` liegen alle Parents flach in der Layer-Wurzel; dort aendert die
    Regel nichts. In `private/` ordnet `remember_private` nach Mandat in Ordner,
    und emit folgt jetzt derselben Ordnung, statt eine zweite daneben
    aufzumachen. Der praktische Gewinn: ein beendetes Mandat laesst sich als
    Ordner am Stueck loeschen, und der Layer hat genau eine Konvention.
    """
    if not parent_ref or parent_ref == "BRAIN":
        return layer_root
    candidate = (_vault_root(layer_root) / parent_ref).parent
    try:
        candidate.relative_to(layer_root)
    except ValueError:
        return layer_root
    return candidate if candidate.is_dir() else layer_root


def _is_distilled(path: Path) -> bool:
    """Erkennt eine von dieser Pipeline erzeugte Note am Frontmatter-Marker."""
    head = path.read_text(encoding="utf-8")[:400]
    return "distilled_from:" in head


def _bump_modified(text: str, stamp: date) -> str:
    """Aktualisiert Frontmatter, ohne den handgeschriebenen Inhalt anzufassen."""
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        return text

    closing = next(
        (idx for idx, line in enumerate(lines[1:], start=1) if line.rstrip("\r\n") == "---"),
        None,
    )
    if closing is None:
        return text

    newline = "\r\n" if lines[0].endswith("\r\n") else "\n"
    value = f"modified: {stamp.isoformat()}{newline}"
    for idx, line in enumerate(lines[1:closing], start=1):
        if line.startswith("modified:"):
            lines[idx] = value
            return "".join(lines)
    lines.insert(closing, value)
    return "".join(lines)


def _frontmatter_value(text: str, key: str) -> str | None:
    """Liest genau einen Wert aus dem ersten Frontmatter-Block."""
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        return None
    closing = next((idx for idx, line in enumerate(lines[1:], start=1) if line == "---"), None)
    if closing is None:
        return None
    values = [
        line.split(":", 1)[1].strip()
        for line in lines[1:closing]
        if line.startswith(f"{key}:")
    ]
    if len(values) != 1:
        return None
    return values[0] or None


def _preserve_created(rendered: str, existing: str) -> str:
    """Ein Re-Emit ist eine Aenderung, keine Neuerstellung der Note."""
    created = _frontmatter_value(existing, "created")
    if created is None:
        return rendered

    lines = rendered.splitlines(keepends=True)
    closing = next(
        (idx for idx, line in enumerate(lines[1:], start=1) if line.rstrip("\r\n") == "---"),
        None,
    )
    if closing is None:
        return rendered
    for idx, line in enumerate(lines[1:closing], start=1):
        if line.startswith("created:"):
            newline = "\r\n" if line.endswith("\r\n") else "\n"
            lines[idx] = f"created: {created}{newline}"
            break
    return "".join(lines)


def _set_parent(text: str, parent: str) -> str:
    """Setzt nur die Parent-Property und laesst den restlichen Inhalt unveraendert."""
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        raise ValueError("bestehende Note ohne Frontmatter kann keinen parent erhalten")
    closing = next(
        (idx for idx, line in enumerate(lines[1:], start=1) if line.rstrip("\r\n") == "---"),
        None,
    )
    if closing is None:
        raise ValueError("bestehende Note hat unvollstaendiges Frontmatter")
    newline = "\r\n" if lines[0].endswith("\r\n") else "\n"
    escaped = parent.replace("\\", "\\\\").replace('"', '\\"')
    value = f'parent: "[[{escaped}]]"{newline}'
    parent_indices = [
        idx
        for idx, line in enumerate(lines[1:closing], start=1)
        if line.startswith("parent:")
    ]
    if len(parent_indices) > 1:
        raise ValueError(
            f"bestehende Note braucht hoechstens einen parent, gefunden {len(parent_indices)}"
        )
    if parent_indices:
        lines[parent_indices[0]] = value
        return "".join(lines)
    lines.insert(closing, value)
    return "".join(lines)


def _split_at_distill_marker(existing: str, marker: str) -> tuple[str, str]:
    """Teilt eine handgeschriebene Nabe in Kopf und das, was NACH dem Block steht.

    Frueher stand hier ``existing.split(marker)[0]``, also "alles ab dem Marker
    ist meins". Das stimmt nur, solange niemand danach noch etwas anhaengt.
    ``remember`` haengt neue Abschnitte aber genau dort an, ans Dateiende. Ein
    spaeterer Emit hat sie damit still geloescht; an ``notes/teamco.md`` sind so
    zwei von Hand geschriebene Abschnitte verschwunden.

    Der Block endet jetzt an der naechsten eigenen ``##``-Ueberschrift. Alles ab
    dort ueberlebt.
    """
    if marker not in existing:
        return existing.rstrip("\n"), ""
    head, _, after = existing.partition(marker)
    following = re.search(r"\n## ", after)
    tail = after[following.start() :].lstrip("\n") if following else ""
    return head.rstrip("\n"), (f"\n{tail}" if tail else "")


def _replace_distill_block(existing: str, addition: str, stamp: date) -> str:
    """Ersetzt nur den markierten Maschinenblock in einer handgeschriebenen Note."""
    block = (
        f"{_DISTILL_START}\n"
        f"## Destillat {stamp.isoformat()}\n\n"
        f"{addition.rstrip()}\n"
        f"{_DISTILL_END}"
    )
    start = existing.find(_DISTILL_START)
    end = existing.find(_DISTILL_END, start + len(_DISTILL_START)) if start >= 0 else -1
    if start < 0:
        return existing.rstrip("\n") + "\n\n" + block + "\n"
    if end < 0:
        raise ValueError("handgeschriebene Note hat unvollstaendigen Destillat-Marker")
    end += len(_DISTILL_END)
    return existing[:start].rstrip("\n") + "\n\n" + block + existing[end:]


def render_hub(
    entity: str,
    groups: dict[str, list[Claim]],
    today: date,
    *,
    parent: str = "",
) -> str:
    """Nabe eines Knotens: verweist datiert auf alle Unterthemen.

    Das ist die Struktur, die den Graphen navigierbar macht. Ohne Nabe liegen
    die Themen verstreut nebeneinander und Obsidian zeigt lauter Inseln.
    """
    lines = [
        "---",
        f"created: {today.isoformat()}",
        f"modified: {today.isoformat()}",
        "ai_generated: true",
    ]
    if parent:
        lines.append(f'parent: "[[{parent}]]"')
    lines.extend(
        [
            f"distilled_from: Nabe ueber {len(groups)} "
            f"{'Thema' if len(groups) == 1 else 'Themen'}",
            "---",
            "",
            f"# {entity}",
            "",
            "> Knoten. Die Unterthemen unten tragen die belegten Einzelaussagen.",
            "",
        ]
    )
    for topic, group in sorted(groups.items()):
        tage = sorted(c.stated_on for c in group if c.stated_on)
        spanne = (
            f"{tage[0].isoformat()} bis {tage[-1].isoformat()}"
            if len(tage) > 1 and tage[0] != tage[-1]
            else (tage[0].isoformat() if tage else "ohne Datum")
        )
        current, _ = resolve(group)
        offen = sum(1 for c in current if c.kind.value == "offen")
        rest = f", {offen} offen" if offen else ""
        ziel = slugify(topic, max_len=60)
        claim_label = "Claim" if len(group) == 1 else "Claims"
        lines.append(f"- [[{ziel}|{topic}]] — {spanne}, {len(group)} {claim_label}{rest}")
    return "\n".join(lines) + "\n"


def emit(
    claims: list[Claim],
    notes_root: Path,
    *,
    today: date | None = None,
    links: dict[str, list[str]] | None = None,
    entity_parents: dict[str, str] | None = None,
    private: bool = False,
) -> list[tuple[Path, bool]]:
    """Schreibt pro Thema eine Note und pro Entitaet eine bereits verankerte Nabe.

    ``private=True`` schreibt in den gitignorierten Layer und laesst nur Parents
    aus demselben Layer zu. Die Aufteilung macht ``obsidiyan.distill.route``,
    diese Funktion bekommt schon getrennte Listen.
    """
    stamp = today or date.today()
    by_topic: dict[str, list[Claim]] = {}
    for claim in claims:
        by_topic.setdefault(claim.topic, []).append(claim)

    # Genau eine Entitaet je Thema. Ein stilles "erster Wert gewinnt" wuerde
    # widerspruechliche Claims unter die falsche Nabe haengen.
    entity_of: dict[str, str] = {}
    for topic, group in by_topic.items():
        entities = {claim.entity.strip() for claim in group if claim.entity.strip()}
        if len(entities) != 1 or any(not claim.entity.strip() for claim in group):
            raise ValueError(f"{topic}: genau eine nicht-leere entity ist Pflicht")
        entity_of[topic] = entities.pop()

    entity_by_slug: dict[str, str] = {}
    for entity in entity_of.values():
        slug = slugify(entity, max_len=60)
        previous = entity_by_slug.setdefault(slug, entity)
        if previous != entity:
            raise ValueError(f"Entity-Slug-Kollision: {previous!r} und {entity!r} -> {slug}")
    topic_by_slug: dict[str, str] = {}
    for topic in by_topic:
        slug = slugify(topic, max_len=60)
        previous = topic_by_slug.setdefault(slug, topic)
        if previous != topic:
            raise ValueError(f"Topic-Slug-Kollision: {previous!r} und {topic!r} -> {slug}")
        if slug in entity_by_slug:
            raise ValueError(
                f"Hub-/Topic-Slug-Kollision: {entity_by_slug[slug]!r} und {topic!r} -> {slug}"
            )

    notes_root.mkdir(parents=True, exist_ok=True)
    written: list[tuple[Path, bool]] = []

    hubs: dict[str, dict[str, list[Claim]]] = {}
    for topic, group in by_topic.items():
        hubs.setdefault(entity_of[topic], {})[topic] = group

    # Vor dem ersten Write muessen alle Naben einen belastbaren Parent haben.
    # Neue Entitaeten werden nie still direkt unter BRAIN abgelegt. Der Parent
    # bestimmt ausserdem den Ordner, deshalb wird er vor jedem Pfad aufgeloest.
    hub_parents: dict[str, str] = {}
    hub_paths: dict[str, Path] = {}
    for entity in hubs:
        hub_path = _find_existing(slugify(entity, max_len=60), notes_root)
        configured = (entity_parents or {}).get(entity) or (entity_parents or {}).get(
            slugify(entity, max_len=60)
        )
        stored = (
            _frontmatter_value(hub_path.read_text(encoding="utf-8"), "parent")
            if hub_path is not None
            else None
        )
        raw_parent = stored or configured or ""
        parent, error = resolve_parent(raw_parent, notes_root, private=private)
        if error:
            raise ValueError(
                f"{entity}: Entity-Nabe braucht vor dem Emit einen bestehenden parent: {error}"
            )
        if hub_path is None:
            hub_path = (
                _dir_beside_parent(parent, notes_root) / f"{slugify(entity, max_len=60)}.md"
            )
        if not is_safe_target(hub_path, notes_root):
            raise ValueError(f"unsicheres Emit-Ziel ausserhalb {notes_root.name}/: {hub_path}")
        hub_paths[entity] = hub_path
        hub_ref = _note_reference(hub_path, notes_root)
        normalized_parent = normalize_reference(parent)
        if normalized_parent is not None and normalized_parent.casefold() == hub_ref.casefold():
            raise ValueError(f"{entity}: Entity-Nabe darf nicht ihr eigener parent sein")
        assignment_error = parent_assignment_error(hub_path, notes_root, parent)
        if assignment_error:
            raise ValueError(f"{entity}: {assignment_error}")
        if hub_path.exists() and not _is_distilled(hub_path):
            _set_parent(hub_path.read_text(encoding="utf-8"), parent)
        hub_parents[entity] = parent

    # Themen liegen neben ihrer Nabe. Eine bestehende Note bleibt, wo sie liegt.
    topic_paths: dict[str, Path] = {}
    for topic in by_topic:
        slug = slugify(topic, max_len=60)
        found = _find_existing(slug, notes_root)
        path = found or (hub_paths[entity_of[topic]].parent / f"{slug}.md")
        if not is_safe_target(path, notes_root):
            raise ValueError(f"unsicheres Emit-Ziel ausserhalb {notes_root.name}/: {path}")
        topic_paths[topic] = path

    # Auch bestehende handgeschriebene Themen werden vor dem ersten Write auf
    # reparierbares Frontmatter geprueft. So ist ein Emit entweder vollstaendig
    # vorbereitet oder mutiert gar nichts.
    for topic in by_topic:
        topic_path = topic_paths[topic]
        if topic_path.exists() and not _is_distilled(topic_path):
            entity_path = hub_paths[entity_of[topic]]
            existing = _set_parent(
                topic_path.read_text(encoding="utf-8"), _note_reference(entity_path, notes_root)
            )
            _replace_distill_block(existing, "preflight", stamp)

    # Naben zuerst schreiben, damit jeder danach erzeugte Themen-Parent existiert.
    for entity, groups in sorted(hubs.items()):
        path = hub_paths[entity]
        path.parent.mkdir(parents=True, exist_ok=True)
        existed = path.exists()
        parent = hub_parents[entity]
        if existed and not _is_distilled(path):
            existing = _set_parent(
                _bump_modified(path.read_text(encoding="utf-8"), stamp), parent
            )
            marker = "## Destillierte Unterthemen"
            block = marker + "\n\n" + "\n".join(
                render_hub(entity, groups, stamp, parent=parent)
                .split("\n\n", 2)[-1]
                .splitlines()
            )
            head, tail = _split_at_distill_marker(existing, marker)
            atomic_write_text(path, head + "\n\n" + block + "\n" + tail)
            written.append((path, False))
            continue
        body = render_hub(entity, groups, stamp, parent=parent)
        if existed:
            body = _preserve_created(body, path.read_text(encoding="utf-8"))
        atomic_write_text(path, body)
        written.append((path, not existed))

    for topic, group in sorted(by_topic.items()):
        auto = list((links or {}).get(topic, []))
        entity = entity_of[topic]
        entity_slug = slugify(entity, max_len=60)
        auto.insert(0, entity_slug)
        topic_parent = _note_reference(hub_paths[entity], notes_root)
        body = render_note(topic, group, stamp, auto or None, parent=topic_parent)
        path = topic_paths[topic]
        path.parent.mkdir(parents=True, exist_ok=True)
        existed = path.exists()

        if existed and not _is_distilled(path):
            # Handgeschriebenes bleibt, nur der markierte Maschinenblock wird ersetzt.
            existing = _set_parent(
                _bump_modified(path.read_text(encoding="utf-8"), stamp), topic_parent
            )
            addition = body.split("---\n", 2)[-1].lstrip("\n")
            atomic_write_text(path, _replace_distill_block(existing, addition, stamp))
        else:
            # Selbst erzeugte Note: vollstaendig neu schreiben. Der Store ist die
            # Wahrheit, sonst dupliziert jeder Lauf seinen eigenen Inhalt.
            if existed:
                body = _preserve_created(body, path.read_text(encoding="utf-8"))
            atomic_write_text(path, body)
        written.append((path, not existed))

    return written
