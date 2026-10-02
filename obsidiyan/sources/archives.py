"""Ingest der eingefrorenen Provider-Exporte in chats/.

Drei Formate, drei Eigenheiten, alle aus einer Inspektion echter Exporte
(08.08.2026):

chatgpt  Konversationen mit `mapping` (node_id -> {id, message, parent}). Das
         children-Feld ist null, der aktive Zweig wird deshalb von `current_node`
         rueckwaerts ueber `parent` rekonstruiert und dann umgedreht.
gemini   Flache Aktivitaetsliste. Ein Eintrag ist EIN Austausch, keine
         Konversation: der Prompt steckt im `title` hinter "Prompted ", die
         Antwort als HTML in `safeHtmlItem`.
claude   Konversationen mit `chat_messages` und `sender` human|assistant.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from obsidiyan.models import Doc, Role, Source, Turn
from obsidiyan.nda import classify
from obsidiyan.sources.harness_noise import is_machine_authored

_GEMINI_PROMPT_PREFIX = "Prompted "
_BLOCK_TAGS = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "hr"}


class _TextExtractor(HTMLParser):
    """Minimaler HTML-zu-Text-Wandler. Keine externe Abhaengigkeit noetig."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def text(self) -> str:
        joined = "".join(self.parts)
        return "\n".join(line.strip() for line in joined.splitlines() if line.strip())


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(unescape(html))
    parser.close()
    return parser.text()


def _epoch(value: object) -> datetime | None:
    if not isinstance(value, int | float):
        return None
    return datetime.fromtimestamp(float(value))


def _iso(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _finish(
    source: Source,
    conv_id: str,
    title: str,
    turns: list[Turn],
    started: datetime | None = None,
    ended: datetime | None = None,
) -> Doc | None:
    if not turns:
        return None
    stamps = [t.ts for t in turns if t.ts]
    return Doc(
        source=source,
        conv_id=conv_id,
        title=title,
        project=source.value,
        started_at=started or (min(stamps) if stamps else None),
        ended_at=ended or (max(stamps) if stamps else None),
        sensitivity=classify(source.value, "\n".join(t.text for t in turns)),
        turns=tuple(turns),
    )


# --------------------------------------------------------------------- chatgpt


def _chatgpt_branch(mapping: dict[str, Any], current: str | None) -> list[dict[str, Any]]:
    """Aktiven Zweig rekonstruieren. children ist null, also rueckwaerts ueber parent."""
    chain: list[dict[str, Any]] = []
    node_id = current
    seen: set[str] = set()
    while node_id and node_id in mapping and node_id not in seen:
        seen.add(node_id)
        node = mapping[node_id]
        if isinstance(node.get("message"), dict):
            chain.append(node["message"])
        node_id = node.get("parent")
    chain.reverse()
    return chain


def _chatgpt_text(message: dict[str, Any]) -> str:
    """Text- und multimodal_text-Nachrichten.

    Bei multimodal_text mischt `parts` Strings mit Bild-/Audio-Deskriptoren. Nur
    die Strings sind Wissen, der Rest ist ein Verweis auf ein Asset, das im
    Export ohnehin fehlt. Reine Asset-Nachrichten fallen dadurch leer heraus.
    """
    content = message.get("content") or {}
    if content.get("content_type") not in ("text", "multimodal_text"):
        return ""
    parts = content.get("parts") or []
    return "\n".join(p for p in parts if isinstance(p, str)).strip()


def parse_chatgpt_file(path: Path) -> Iterator[Doc]:
    for conv in json.loads(path.read_text(encoding="utf-8", errors="replace")):
        if not isinstance(conv, dict):
            continue
        mapping = conv.get("mapping") or {}
        turns: list[Turn] = []
        for message in _chatgpt_branch(mapping, conv.get("current_node")):
            role_raw = (message.get("author") or {}).get("role")
            if role_raw not in ("user", "assistant"):
                continue
            text = _chatgpt_text(message)
            if text:
                role = Role(role_raw)
                turns.append(
                    Turn(
                        role=role,
                        ts=_epoch(message.get("create_time")),
                        text=text,
                        machine_summary=role is Role.USER and is_machine_authored(text),
                    )
                )
        doc = _finish(
            Source.CHATGPT,
            str(conv.get("conversation_id") or conv.get("id") or path.stem),
            str(conv.get("title") or ""),
            turns,
            started=_epoch(conv.get("create_time")),
            ended=_epoch(conv.get("update_time")),
        )
        if doc is not None:
            yield doc


# ---------------------------------------------------------------------- gemini


def parse_gemini_file(path: Path) -> Iterator[Doc]:
    entries = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        title = str(entry.get("title") or "")
        prompt = title.removeprefix(_GEMINI_PROMPT_PREFIX).strip()
        stamp = _iso(entry.get("time"))
        answer = "\n\n".join(
            html_to_text(str(item.get("html") or ""))
            for item in (entry.get("safeHtmlItem") or [])
            if isinstance(item, dict)
        ).strip()

        turns: list[Turn] = []
        if prompt:
            turns.append(
                Turn(
                    role=Role.USER,
                    ts=stamp,
                    text=prompt,
                    machine_summary=is_machine_authored(prompt),
                )
            )
        if answer:
            turns.append(Turn(role=Role.ASSISTANT, ts=stamp, text=answer))

        doc = _finish(Source.GEMINI, f"{path.stem}-{index}", prompt[:80], turns, stamp, stamp)
        if doc is not None:
            yield doc


# ---------------------------------------------------------------- claude (web)

_CLAUDE_ROLES = {"human": Role.USER, "assistant": Role.ASSISTANT}


def parse_claude_file(path: Path) -> Iterator[Doc]:
    for conv in json.loads(path.read_text(encoding="utf-8", errors="replace")):
        if not isinstance(conv, dict):
            continue
        turns: list[Turn] = []
        for message in conv.get("chat_messages") or []:
            role = _CLAUDE_ROLES.get(str(message.get("sender")))
            if role is None:
                continue
            text = str(message.get("text") or "").strip()
            if not text:
                blocks = message.get("content") or []
                text = "\n".join(
                    str(b.get("text") or "")
                    for b in blocks
                    if isinstance(b, dict) and b.get("type") == "text"
                ).strip()
            if text:
                turns.append(
                    Turn(
                        role=role,
                        ts=_iso(message.get("created_at")),
                        text=text,
                        machine_summary=role is Role.USER and is_machine_authored(text),
                    )
                )
        doc = _finish(
            Source.CLAUDE_WEB,
            str(conv.get("uuid") or path.stem),
            str(conv.get("name") or ""),
            turns,
            started=_iso(conv.get("created_at")),
            ended=_iso(conv.get("updated_at")),
        )
        if doc is not None:
            yield doc


PARSERS = {
    Source.CHATGPT: (("chatgpt", "conversations-*.json"), parse_chatgpt_file),
    Source.GEMINI: (("gemini", "*activity*.json"), parse_gemini_file),
    Source.CLAUDE_WEB: (("claude", "conversations.json"), parse_claude_file),
}


def iter_archive_files(source: Source, chats_root: Path) -> Iterator[Path]:
    (folder, pattern), _ = PARSERS[source]
    base = chats_root / folder
    if base.exists():
        yield from sorted(base.glob(pattern))
