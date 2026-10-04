"""Codex CLI ab etwa Juli 2026 schreibt Nachrichten nur noch als response_item.

Die frueheren event_msg-Typen user_message und agent_message fehlen dort, der
alte Import lieferte deshalb fuer jede Session null Turns. Die Fixture bildet
die Struktur echter Rollouts nach, mit erfundenem Text.
"""

from __future__ import annotations

import json
from pathlib import Path

from obsidiyan.models import Role
from obsidiyan.sources import codex


def _msg(role: str, *texts: str) -> dict:
    kind = "input_text" if role in ("user", "developer") else "output_text"
    return {
        "timestamp": "2026-09-20T10:00:00.000Z",
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": role,
            "content": [{"type": kind, "text": t} for t in texts],
        },
    }


def _write(tmp_path: Path, rows: list[dict]) -> Path:
    path = tmp_path / "rollout-2026-09-20T10-00-00-abc.jsonl"
    meta = {
        "timestamp": "2026-09-20T10:00:00.000Z",
        "type": "session_meta",
        "payload": {"id": "conv-new", "cwd": "/home/demo/shop", "cli_version": "0.160.0"},
    }
    path.write_text("".join(json.dumps(r) + "\n" for r in [meta, *rows]), encoding="utf-8")
    return path


def test_reads_messages_from_response_items(tmp_path: Path) -> None:
    doc = codex.parse_session(
        _write(
            tmp_path,
            [
                _msg("developer", "You are Codex, a coding agent."),
                _msg(
                    "user",
                    "<environment_context>\n<cwd>/home/demo/shop</cwd>\n</environment_context>",
                    "# AGENTS.md instructions for /home/demo/shop\n\nUse pytest.",
                    "Why do we use Postgres for the shop?",
                ),
                _msg("assistant", "Because checkout needs one transaction."),
                {
                    "timestamp": "2026-09-20T10:00:01.000Z",
                    "type": "event_msg",
                    "payload": {
                        "type": "item_completed",
                        "item": {"type": "AgentMessage", "content": []},
                    },
                },
                {
                    "timestamp": "2026-09-20T10:00:01.000Z",
                    "type": "response_item",
                    "payload": {"type": "reasoning", "summary": [], "content": None},
                },
            ],
        )
    )
    assert doc is not None
    assert doc.conv_id == "conv-new"
    assert [(t.role, t.text) for t in doc.turns] == [
        (Role.USER, "Why do we use Postgres for the shop?"),
        (Role.ASSISTANT, "Because checkout needs one transaction."),
    ]


def test_drops_injected_context_and_tool_transcripts(tmp_path: Path) -> None:
    doc = codex.parse_session(
        _write(
            tmp_path,
            [
                _msg("user", "<recommended_plugins>a, b</recommended_plugins>"),
                _msg("user", "<system_instruction>be brief</system_instruction>"),
                _msg("user", "<ide_opened_file>src/app.py</ide_opened_file>"),
                _msg("user", "[Request interrupted by user for tool use]"),
                _msg(
                    "assistant",
                    "[external_agent_tool_call: Read]\nfile: src/app.py\n"
                    "[/external_agent_tool_call]",
                ),
                _msg("assistant", "[external_agent_tool_result]\n[/external_agent_tool_result]"),
                _msg("user", "Add a health endpoint."),
            ],
        )
    )
    assert doc is not None
    assert [t.text for t in doc.turns] == ["Add a health endpoint."]


def test_keeps_only_the_request_from_ide_context(tmp_path: Path) -> None:
    doc = codex.parse_session(
        _write(
            tmp_path,
            [
                _msg(
                    "user",
                    "# Context from my IDE setup:\n\n## Open tabs:\n- app.py\n\n"
                    "## My request for Codex:\nRename the settings module.",
                ),
            ],
        )
    )
    assert doc is not None
    assert doc.turns[0].text == "Rename the settings module."


def test_scheduled_task_prompts_are_not_user_statements(tmp_path: Path) -> None:
    doc = codex.parse_session(
        _write(
            tmp_path,
            [
                _msg(
                    "user", '<scheduled-task name="nightly-check">Check the build.</scheduled-task>'
                ),
                _msg("assistant", "The build is green."),
            ],
        )
    )
    assert doc is not None
    assert doc.turns[0].machine_summary is True
