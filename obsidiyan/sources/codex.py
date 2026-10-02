"""Ingest fuer Codex-Rollout-Transkripte (~/.codex/sessions/**/rollout-*.jsonl).

Behalten werden nur die event_msg-Paare user_message und agent_message. Die
response_item/message-Zeilen tragen denselben Text nochmal und wuerden den Corpus
verdoppeln; reasoning, function_call, function_call_output und token_count sind
Maschinenverkehr.

Schema aus einer Inspektion echter Rollouts (08.08.2026).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from obsidiyan.models import Doc, Role, Source, Turn
from obsidiyan.nda import classify
from obsidiyan.sources.harness_noise import is_machine_authored, strip_noise

SESSIONS_ROOT = Path.home() / ".codex" / "sessions"

_ROLE_BY_PAYLOAD = {"user_message": Role.USER, "agent_message": Role.ASSISTANT}


def _ts(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_session(path: Path) -> Doc | None:
    turns: list[Turn] = []
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

