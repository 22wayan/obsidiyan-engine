from __future__ import annotations

import json
from pathlib import Path

from obsidiyan.models import Role, Source
from obsidiyan.sources import archives


def test_html_to_text_strips_markup() -> None:
    text = archives.html_to_text("<p>Hallo <strong>Welt</strong></p><hr><p>Zweiter Absatz</p>")
    assert "<" not in text
    assert "Hallo Welt" in text
    assert "Zweiter Absatz" in text


def test_chatgpt_linearises_active_branch(tmp_path: Path) -> None:
    """children ist null, der Zweig muss ueber parent von current_node rekonstruiert werden."""
    conv = {
        "conversation_id": "c1",
        "title": "Titel",
        "create_time": 1_780_000_000,
        "update_time": 1_780_000_100,
        "current_node": "n3",
        "mapping": {
            "n0": {"id": "n0", "message": None, "parent": None},
            "n1": {
                "id": "n1",
                "parent": "n0",
                "message": {
                    "author": {"role": "user"},
                    "content": {"content_type": "text", "parts": ["frage"]},
                    "create_time": 1_780_000_000,
                },
            },
            "n2": {
                "id": "n2",
                "parent": "n1",
                "message": {
                    "author": {"role": "assistant"},
                    "content": {"content_type": "text", "parts": ["antwort"]},
                    "create_time": 1_780_000_050,
                },
            },
            "n3": {
                "id": "n3",
                "parent": "n2",
                "message": {
                    "author": {"role": "user"},
                    "content": {"content_type": "text", "parts": ["nachfrage"]},
                    "create_time": 1_780_000_100,
                },
            },
            "verworfen": {
                "id": "verworfen",
                "parent": "n1",
                "message": {
                    "author": {"role": "assistant"},
                    "content": {"content_type": "text", "parts": ["TOTER ZWEIG"]},
                    "create_time": 1_780_000_060,
                },
            },
        },
    }
    path = tmp_path / "conversations-000.json"
    path.write_text(json.dumps([conv]), encoding="utf-8")

    docs = list(archives.parse_chatgpt_file(path))
    assert len(docs) == 1
    texts = [t.text for t in docs[0].turns]
    assert texts == ["frage", "antwort", "nachfrage"]
    assert "TOTER ZWEIG" not in "".join(texts)
    assert docs[0].source is Source.CHATGPT
    assert docs[0].title == "Titel"


def test_gemini_entry_becomes_exchange(tmp_path: Path) -> None:
    entry = {
        "title": "Prompted Sag mir mehr zur Uhr",
        "time": "2026-07-25T20:16:30.369Z",
        "safeHtmlItem": [{"html": "<p>Die <strong>Seiko</strong> ist legendaer.</p>"}],
    }
    path = tmp_path / "gemini-apps-activity.json"
    path.write_text(json.dumps([entry]), encoding="utf-8")

    docs = list(archives.parse_gemini_file(path))
    assert len(docs) == 1
    assert [t.role for t in docs[0].turns] == [Role.USER, Role.ASSISTANT]
    assert docs[0].turns[0].text == "Sag mir mehr zur Uhr"
    assert "Seiko" in docs[0].turns[1].text
    assert "<p>" not in docs[0].turns[1].text


def test_claude_web_maps_sender(tmp_path: Path) -> None:
    conv = {
        "uuid": "u1",
        "name": "Ein Chat",
        "created_at": "2026-07-30T15:47:27Z",
        "updated_at": "2026-07-30T15:50:00Z",
        "chat_messages": [
            {"sender": "human", "text": "frage", "created_at": "2026-07-30T15:47:27Z"},
            {
                "sender": "assistant",
                "text": "",
                "content": [{"type": "text", "text": "antwort aus content"}],
                "created_at": "2026-07-30T15:48:00Z",
            },
        ],
    }
    path = tmp_path / "conversations.json"
    path.write_text(json.dumps([conv]), encoding="utf-8")

    docs = list(archives.parse_claude_file(path))
    assert len(docs) == 1
    assert [t.text for t in docs[0].turns] == ["frage", "antwort aus content"]
    assert docs[0].source is Source.CLAUDE_WEB


def test_chatgpt_multimodal_keeps_text_parts(tmp_path: Path) -> None:
    """multimodal_text mischt Strings mit Asset-Deskriptoren. Die Strings sind das Wissen."""
    conv = {
        "conversation_id": "c2",
        "title": "Mit Bild",
        "current_node": "n1",
        "mapping": {
            "n1": {
                "id": "n1",
                "parent": None,
                "message": {
                    "author": {"role": "user"},
                    "content": {
                        "content_type": "multimodal_text",
                        "parts": [{"asset_pointer": "file-abc"}, "was ist auf dem Bild"],
                    },
                    "create_time": 1_780_000_000,
                },
            }
        },
    }
    path = tmp_path / "conversations-001.json"
    path.write_text(json.dumps([conv]), encoding="utf-8")
    docs = list(archives.parse_chatgpt_file(path))
    assert len(docs) == 1
    assert docs[0].turns[0].text == "was ist auf dem Bild"
