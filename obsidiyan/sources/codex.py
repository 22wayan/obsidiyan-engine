"""Ingest fuer Codex-Rollout-Transkripte (~/.codex/sessions/**/rollout-*.jsonl).

Zwei Formate:

- Aeltere Rollouts tragen jede Nachricht zweimal, als event_msg (user_message,
  agent_message) und als response_item. Gelesen werden dann nur die event_msg.
- Neuere Rollouts (Codex CLI ab etwa 0.142) haben keine user_message und
  agent_message mehr. Dann werden die response_item-Nachrichten mit Rolle user
  und assistant gelesen. Codex spielt dort Umgebung, AGENTS.md, Plugin-Listen
  und Systemanweisungen als eigene Inhaltsbloecke ein; die fallen weg.

reasoning, function_call, custom_tool_call, token_count und item_completed sind
Maschinenverkehr. Schema aus einer Inspektion echter Rollouts (04.10.2026).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from obsidiyan.models import Doc, Role, Source, Turn
from obsidiyan.nda import classify
from obsidiyan.sources.harness_noise import is_machine_authored, strip_noise

SESSIONS_ROOT = Path.home() / ".codex" / "sessions"

_ROLE_BY_PAYLOAD = {"user_message": Role.USER, "agent_message": Role.ASSISTANT}
_ROLE_BY_MESSAGE = {"user": Role.USER, "assistant": Role.ASSISTANT}

# Inhaltsbloecke, die Codex selbst in die Nutzer-Nachricht einspielt.
_INJECTED_PREFIXES = (
    "<environment_context",
    "<user_instructions",
    "<recommended_plugins",
    "<system_instruction",
    "<ide_opened_file",
    "<permissions",
    "<skills_instructions",
    "<collaboration_mode",
    "# AGENTS.md instructions",
    "[Request interrupted by user",
    "<turn_aborted",
    "<external_codex_apps_open_page",
)
# Protokollierte Werkzeug-Aufrufe im Antworttext importierter Agent-Sessions.
_TOOL_TRANSCRIPT_PREFIXES = ("[external_agent_tool_call", "[external_agent_tool_result")
_IDE_REQUEST = re.compile(r"^## My request for Codex:\s*(.*)\Z", re.MULTILINE | re.DOTALL)
_SCHEDULED_TASK = re.compile(r"^\s*<scheduled-task\b", re.IGNORECASE)


def _user_block(text: str) -> str:
    """Nutzertext eines Inhaltsblocks, eingespielter Kontext wird zu leerem Text."""
    stripped = text.lstrip()
    if stripped.startswith(_INJECTED_PREFIXES):
        return ""
    if stripped.startswith("# Context from my IDE setup"):
        match = _IDE_REQUEST.search(stripped)
        return match.group(1).strip() if match else ""
    return text


def _message_text(payload: dict[str, object], role: Role) -> str:
    content = payload.get("content")
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "")
        if role is Role.USER:
            text = _user_block(text)
        elif text.lstrip().startswith(_TOOL_TRANSCRIPT_PREFIXES):
            text = ""
        if text.strip():
            parts.append(text.strip())
    return "\n\n".join(parts)


def _ts(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_session(path: Path) -> Doc | None:
    turns: list[Turn] = []
    item_turns: list[Turn] = []
    cwd = ""
    conv_id = path.stem
    meta_seen = False

    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            payload = obj.get("payload")
            if not isinstance(payload, dict):
                continue

            if obj.get("type") == "session_meta":
                # Eine Datei kann mehrere session_meta-Zeilen haben (geforkte Threads).
                # Die erste gewinnt, sonst kollidieren verschiedene Rollouts auf einer id.
                cwd = cwd or str(payload.get("cwd") or "")
                if not meta_seen:
                    conv_id = str(payload.get("id") or conv_id)
                    meta_seen = True
                continue

            if obj.get("type") == "response_item" and payload.get("type") == "message":
                item_role = _ROLE_BY_MESSAGE.get(str(payload.get("role")))
                if item_role is not None:
                    text = strip_noise(_message_text(payload, item_role))
                    if text:
                        machine = item_role is Role.USER and (
                            is_machine_authored(text) or bool(_SCHEDULED_TASK.match(text))
                        )
                        item_turns.append(
                            Turn(
                                role=item_role,
                                ts=_ts(obj.get("timestamp")),
                                text=text,
                                machine_summary=machine,
                            )
                        )
                continue

            role = _ROLE_BY_PAYLOAD.get(str(payload.get("type")))
            if role is None:
                continue
            text = strip_noise(str(payload.get("message") or ""))
            if not text:
                continue
            machine = role is Role.USER and is_machine_authored(text)
            turns.append(
                Turn(role=role, ts=_ts(obj.get("timestamp")), text=text, machine_summary=machine)
            )

    # Alte Rollouts haben beide Formen; dann gewinnen die event_msg-Turns,
    # sonst stuende jede Nachricht doppelt im Corpus.
    if not turns:
        turns = item_turns
    if not turns:
        return None

    project = Path(cwd).name if cwd else ""
    stamps = [t.ts for t in turns if t.ts]
    return Doc(
        source=Source.CODEX,
        conv_id=conv_id,
        title=_first_line(turns),
        project=project,
        started_at=min(stamps) if stamps else None,
        ended_at=max(stamps) if stamps else None,
        sensitivity=classify(project, "\n".join(t.text for t in turns)),
        cwd=cwd,
        turns=tuple(turns),
    )


def _first_line(turns: list[Turn]) -> str:
    for turn in turns:
        if turn.role is Role.USER:
            for line in turn.text.splitlines():
                cleaned = line.strip().lstrip("#").strip()
                if cleaned:
                    return cleaned[:80]
    return ""


def iter_sessions(root: Path | None = None) -> Iterator[Path]:
    base = root or SESSIONS_ROOT
    if not base.exists():
        return
    yield from sorted(base.rglob("rollout-*.jsonl"))
