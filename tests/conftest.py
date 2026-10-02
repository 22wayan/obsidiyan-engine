from __future__ import annotations

import os

# Claude Code kodiert das Home-Verzeichnis in den Projekt-Slug. Die Tests
# benutzen ein festes Fantasie-Home, unabhaengig vom Rechner.
os.environ.setdefault("OBSIDIYAN_SLUG_PREFIX", "-Users-demo-")

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from obsidiyan.models import Doc, Role, Sensitivity, Source, Turn

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def repo_root() -> Path:
    return REPO_ROOT


def make_doc(
    *,
    source: Source = Source.CLAUDE_CODE,
    conv_id: str = "conv-1",
    title: str = "Ein Titel",
    project: str = "demo-projekt",
    day: str = "2026-08-01",
    sensitivity: Sensitivity = Sensitivity.CLEAN,
    turns: tuple[Turn, ...] | None = None,
) -> Doc:
    start = datetime.fromisoformat(f"{day}T10:00:00+00:00")
    default = (
        Turn(role=Role.USER, ts=start, text="wie war die Entscheidung"),
        Turn(role=Role.ASSISTANT, ts=start, text="so und so"),
    )
    return Doc(
        source=source,
        conv_id=conv_id,
        title=title,
        project=project,
        started_at=start,
        ended_at=start,
        sensitivity=sensitivity,
        turns=turns if turns is not None else default,
    )


def write_claude_session(
    projects_root: Path,
    *,
    slug: str = "-Users-demo-demo",
    session_id: str = "sess-1",
    lines: list[dict[str, object]] | None = None,
) -> Path:
    folder = projects_root / slug
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{session_id}.jsonl"
    payload = lines if lines is not None else default_claude_lines(session_id)
    path.write_text(
        "\n".join(json.dumps(line, ensure_ascii=False) for line in payload) + "\n",
        encoding="utf-8",
    )
    return path


def default_claude_lines(session_id: str) -> list[dict[str, object]]:
    stamp = datetime(2026, 8, 1, 10, 0, tzinfo=UTC).isoformat()
    return [
        {"type": "ai-title", "aiTitle": "Beispielsitzung", "sessionId": session_id},
        {
            "type": "user",
            "sessionId": session_id,
            "timestamp": stamp,
            "cwd": "/Users/demo/demo",
            "gitBranch": "main",
            "message": {"role": "user", "content": "echte Nutzerfrage"},
        },
        {
            "type": "assistant",
            "sessionId": session_id,
            "timestamp": stamp,
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "GEHEIMES DENKEN"},
                    {"type": "text", "text": "sichtbare Antwort"},
                    {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
                ],
            },
        },
        {
            "type": "user",
            "sessionId": session_id,
            "timestamp": stamp,
            "message": {
                "role": "user",
                "content": [{"type": "tool_result", "content": "TOOL AUSGABE"}],
            },
        },
        {
            "type": "user",
            "isMeta": True,
            "sessionId": session_id,
            "timestamp": stamp,
            "message": {"role": "user", "content": "META RAUSCHEN"},
        },
        {
            "type": "user",
            "isSidechain": True,
            "sessionId": session_id,
            "timestamp": stamp,
            "message": {"role": "user", "content": "SIDECHAIN RAUSCHEN"},
        },
        {"type": "file-history-snapshot", "snapshot": {"x": 1}},
    ]


def write_codex_session(
    sessions_root: Path, *, rollout_id: str = "roll-1", cwd: str = "/Users/demo/demo"
) -> Path:
    folder = sessions_root / "2026" / "08" / "01"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime(2026, 8, 1, 12, 0, tzinfo=UTC).isoformat()
    lines: list[dict[str, object]] = [
        {
            "type": "session_meta",
            "timestamp": stamp,
            "payload": {"id": rollout_id, "cwd": cwd},
        },
        {
            "type": "event_msg",
            "timestamp": stamp,
            "payload": {"type": "user_message", "message": "codex nutzerfrage"},
        },
        {
            "type": "event_msg",
            "timestamp": stamp,
            "payload": {"type": "agent_message", "message": "codex antwort"},
        },
        {
            "type": "response_item",
            "timestamp": stamp,
            "payload": {"type": "reasoning", "summary": "DENKEN"},
        },
        {
            "type": "event_msg",
            "timestamp": stamp,
            "payload": {"type": "token_count", "total": 42},
        },
        {
            "type": "response_item",
            "timestamp": stamp,
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "codex nutzerfrage"}],
            },
        },
    ]
    path = folder / f"rollout-2026-08-01T12-00-00-{rollout_id}.jsonl"
    path.write_text(
        "\n".join(json.dumps(line, ensure_ascii=False) for line in lines) + "\n",
        encoding="utf-8",
    )
    return path
