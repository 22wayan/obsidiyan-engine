from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from conftest import write_claude_session

from obsidiyan import ingest
from obsidiyan.corpusio import iter_docs, read_doc
from obsidiyan.models import Role, Sensitivity
from obsidiyan.sources import claude_code


def test_distillation_drops_machine_traffic(tmp_path: Path) -> None:
    path = write_claude_session(tmp_path / "projects")
    doc = claude_code.parse_session(path)
    assert doc is not None
    body = "\n".join(t.text for t in doc.turns)
    for noise in ("GEHEIMES DENKEN", "TOOL AUSGABE", "META RAUSCHEN", "SIDECHAIN RAUSCHEN"):
        assert noise not in body
    assert "echte Nutzerfrage" in body
    assert "sichtbare Antwort" in body


def test_roles_and_metadata(tmp_path: Path) -> None:
    doc = claude_code.parse_session(write_claude_session(tmp_path / "projects"))
    assert doc is not None
    assert [t.role for t in doc.turns] == [Role.USER, Role.ASSISTANT]
    assert doc.title == "Beispielsitzung"
    assert doc.project == "demo"
    assert doc.git_branch == "main"
    assert doc.started_at is not None


def test_nda_project_is_flagged(tmp_path: Path) -> None:
    path = write_claude_session(
        tmp_path / "projects", slug="-Users-demo-CRM-globex", session_id="s2"
    )
    doc = claude_code.parse_session(path)
    assert doc is not None
    assert doc.sensitivity is Sensitivity.NDA


def test_session_without_real_turns_is_skipped(tmp_path: Path) -> None:
    path = write_claude_session(
        tmp_path / "projects",
        session_id="empty",
        lines=[{"type": "file-history-snapshot", "snapshot": {}}],
    )
    assert claude_code.parse_session(path) is None


def test_truncated_last_line_does_not_crash(tmp_path: Path) -> None:
    path = write_claude_session(tmp_path / "projects")
    with path.open("a", encoding="utf-8") as fh:
        fh.write('{"type": "user", "message": {"role": "user", "cont')
    doc = claude_code.parse_session(path)
    assert doc is not None


def test_run_writes_corpus_and_is_incremental(tmp_path: Path) -> None:
    projects = tmp_path / "projects"
    corpus = tmp_path / "corpus"
    write_claude_session(projects)

    first = ingest.run(
        "claude-code", corpus, session_root=projects, require_source_overrides=False
    )
    assert first.written == 1
    assert len(list(iter_docs(corpus))) == 1

    second = ingest.run(
        "claude-code", corpus, session_root=projects, require_source_overrides=False
    )
    assert second.written == 0
    assert second.skipped_unchanged == 1


def test_appended_session_is_reingested(tmp_path: Path) -> None:
    """Der eigentliche Grund fuer (size, mtime) statt nur mtime."""
    projects = tmp_path / "projects"
    corpus = tmp_path / "corpus"
    path = write_claude_session(projects)
    ingest.run(
        "claude-code", corpus, session_root=projects, require_source_overrides=False
    )

    with path.open("a", encoding="utf-8") as fh:
        fh.write(
            '{"type": "user", "sessionId": "sess-1", "message":'
            ' {"role": "user", "content": "nachtraeglicher Satz"}}\n'
        )

    again = ingest.run(
        "claude-code", corpus, session_root=projects, require_source_overrides=False
    )
    assert again.written == 1
    docs = [read_doc(p) for p in iter_docs(corpus)]
    assert any("nachtraeglicher Satz" in t.text for d in docs for t in d.turns)


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    projects = tmp_path / "projects"
    corpus = tmp_path / "corpus"
    write_claude_session(projects)
    stats = ingest.run(
        "claude-code",
        corpus,
        session_root=projects,
        dry_run=True,
        require_source_overrides=False,
    )
    assert stats.written == 1
    assert list(iter_docs(corpus)) == []


def test_harness_noise_is_stripped_from_user_turns(tmp_path: Path) -> None:
    """Slash-Command-Wrapper und system-reminder hat der Nutzer nie getippt."""
    path = write_claude_session(
        tmp_path / "projects",
        session_id="noise",
        lines=[
            {
                "type": "user",
                "sessionId": "noise",
                "timestamp": "2026-08-06T12:00:00Z",
                "message": {
                    "role": "user",
                    "content": (
                        "<command-message>insights</command-message>\n"
                        "<command-name>/insights</command-name>\n"
                        "echte Frage dahinter"
                    ),
                },
            },
            {
                "type": "user",
                "sessionId": "noise",
                "timestamp": "2026-08-06T12:01:00Z",
                "message": {
                    "role": "user",
                    "content": "<system-reminder>IGNORIER MICH</system-reminder>",
                },
            },
        ],
    )
    doc = claude_code.parse_session(path)
    assert doc is not None
    body = "\n".join(t.text for t in doc.turns)
    assert "IGNORIER MICH" not in body
    assert "command-message" not in body
    assert "echte Frage dahinter" in body
    assert "/insights" in body
    assert len(doc.turns) == 1


def test_home_project_slug_is_normalised(tmp_path: Path) -> None:
    path = write_claude_session(
        tmp_path / "projects", slug="-Users-demo", session_id="home"
    )
    doc = claude_code.parse_session(path)
    assert doc is not None
    assert doc.project == "_home"


def test_subagent_transcripts_are_not_sessions(tmp_path: Path) -> None:
    """Subagent-Transkripte liegen tiefer und sind Maschinenverkehr, kein Wissen."""
    projects = tmp_path / "projects"
    write_claude_session(projects, session_id="echt")
    deep = projects / "-Users-demo-demo" / "echt" / "subagents"
    deep.mkdir(parents=True, exist_ok=True)
    (deep / "agent-abc.jsonl").write_text("{}\n", encoding="utf-8")

    found = [p.name for p in claude_code.iter_sessions(projects)]
    assert found == ["echt.jsonl"]


def test_origin_repo_resolves_worktree_to_main_repo(tmp_path: Path) -> None:
    """Ein Worktree ausserhalb des Repos verliert den Repo-Namen aus dem Pfad.
    origin_repo muss ihn ueber git zurueckholen, sonst greift die pfadbasierte
    NDA-Regel dort nicht mehr."""
    import subprocess

    from obsidiyan.sources.claude_code import origin_repo

    repo = tmp_path / "kundenprojekt"
    repo.mkdir()
    run = lambda *a: subprocess.run(  # noqa: E731
        ["git", "-C", str(repo), *a], capture_output=True, check=True
    )
    run("init", "-q")
    run("config", "user.email", "t@example.invalid")
    run("config", "user.name", "t")
    (repo / "x.txt").write_text("x", encoding="utf-8")
    run("add", "-A")
    run("commit", "-q", "-m", "init")

    # Neutral benannter Worktree ausserhalb des Repos: der Risikofall.
    wt = tmp_path / "neutraler-name"
    run("worktree", "add", "-q", "-b", "wt", str(wt))

    assert origin_repo(str(repo)) == "kundenprojekt"
    assert origin_repo(str(wt)) == "kundenprojekt"


def test_origin_repo_is_silent_outside_git(tmp_path: Path) -> None:
    """Kein Repo, kein Verzeichnis, kein Krach: der Aufrufer nutzt den Wert
    nur zusaetzlich, ein leerer Rueckgabewert darf nichts kaputt machen."""
    from obsidiyan.sources.claude_code import origin_repo

    plain = tmp_path / "kein-repo"
    plain.mkdir()
    assert origin_repo(str(plain)) == ""
    assert origin_repo(str(tmp_path / "gibt-es-nicht")) == ""
    assert origin_repo("") == ""


def test_automation_runs_are_not_ingested(tmp_path: Path) -> None:
    """Die Destillations-Automatik ruft Claude Code headless auf. Ohne Filter
    wuerden diese Laeufe selbst zu Kandidaten und die Automatik fuettert sich
    endlos selbst."""
    from obsidiyan.sources.claude_code import parse_session

    stamp = datetime(2026, 8, 18, 21, 22, tzinfo=UTC).isoformat()
    lines = [
        {
            "type": "user",
            "sessionId": "auto-1",
            "timestamp": stamp,
            "cwd": "/Users/demo/Obsidiyan",
            "message": {
                "role": "user",
                "content": "<!-- obsidiyan-automation: maschineller Lauf -->\nDu destillierst...",
            },
        },
        {
            "type": "assistant",
            "sessionId": "auto-1",
            "timestamp": stamp,
            "message": {"role": "assistant", "content": [{"type": "text", "text": "erledigt"}]},
        },
    ]
    path = write_claude_session(tmp_path, session_id="auto-1", lines=lines)
    assert parse_session(path) is None


def test_marker_later_in_conversation_does_not_exclude(tmp_path: Path) -> None:
    """Nur der erste Nutzer-Turn zaehlt. Wer den Marker spaeter zitiert, etwa
    beim Bauen genau dieses Filters, darf seine Sitzung nicht verlieren."""
    from obsidiyan.sources.claude_code import parse_session

    stamp = datetime(2026, 8, 18, 21, 22, tzinfo=UTC).isoformat()
    lines = [
        {
            "type": "user",
            "sessionId": "echt-1",
            "timestamp": stamp,
            "cwd": "/Users/demo/Obsidiyan",
            "message": {"role": "user", "content": "baue mir einen Filter"},
        },
        {
            "type": "assistant",
            "sessionId": "echt-1",
            "timestamp": stamp,
            "message": {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
        },
        {
            "type": "user",
            "sessionId": "echt-1",
            "timestamp": stamp,
            "message": {"role": "user", "content": "der Marker heisst obsidiyan-automation"},
        },
    ]
    path = write_claude_session(tmp_path, session_id="echt-1", lines=lines)
    doc = parse_session(path)
    assert doc is not None
    assert len(doc.turns) == 3


def test_worktree_session_is_attributed_to_the_main_repo(tmp_path: Path) -> None:
    """Ein Workspace-Manager legt pro Session einen Worktree an. Ohne
    Aufloesung erscheint jeder davon als eigenes Projekt und Zeitachse wie
    Projektfilter zerfasern."""
    import subprocess

    from obsidiyan.sources.claude_code import parse_session

    repo = tmp_path / "kundenprojekt"
    repo.mkdir()
    run = lambda *a: subprocess.run(  # noqa: E731
        ["git", "-C", str(repo), *a], capture_output=True, check=True
    )
    run("init", "-q")
    run("config", "user.email", "t@example.invalid")
    run("config", "user.name", "t")
    (repo / "x.txt").write_text("x", encoding="utf-8")
    run("add", "-A")
    run("commit", "-q", "-m", "init")
    wt = tmp_path / "workspaces" / "tripoli"
    wt.parent.mkdir(parents=True, exist_ok=True)
    run("worktree", "add", "-q", "-b", "wt", str(wt))

    stamp = datetime(2026, 8, 19, 3, 0, tzinfo=UTC).isoformat()
    lines = [
        {
            "type": "user",
            "sessionId": "wt-1",
            "timestamp": stamp,
            "cwd": str(wt),
            "message": {"role": "user", "content": "echte Frage im Worktree"},
        },
        {
            "type": "assistant",
            "sessionId": "wt-1",
            "timestamp": stamp,
            "message": {"role": "assistant", "content": [{"type": "text", "text": "Antwort"}]},
        },
    ]
    path = write_claude_session(tmp_path / "projects", session_id="wt-1", lines=lines)
    doc = parse_session(path)
    assert doc is not None
    assert doc.project == "kundenprojekt"


def test_unresolvable_cwd_keeps_the_directory_slug(tmp_path: Path) -> None:
    """Ist das Verzeichnis weg oder kein Repo, bleibt es beim bisherigen
    Verhalten statt die Zuordnung zu verlieren."""
    from obsidiyan.sources.claude_code import parse_session, project_slug

    stamp = datetime(2026, 8, 19, 3, 0, tzinfo=UTC).isoformat()
    lines = [
        {
            "type": "user",
            "sessionId": "weg-1",
            "timestamp": stamp,
            "cwd": "/gibt/es/nicht/mehr",
            "message": {"role": "user", "content": "Frage"},
        },
        {
            "type": "assistant",
            "sessionId": "weg-1",
            "timestamp": stamp,
            "message": {"role": "assistant", "content": [{"type": "text", "text": "Antwort"}]},
        },
    ]
    path = write_claude_session(tmp_path, session_id="weg-1", lines=lines)
    doc = parse_session(path)
    assert doc is not None
    assert doc.project == project_slug(path)


def test_ingest_never_writes_a_secret_into_the_corpus(tmp_path: Path) -> None:

    fake = "an_" + "sk_" + "a1B2c3D4" * 4
    project = tmp_path / "sessions" / "-Users-demo-shop"
    project.mkdir(parents=True)
    rows = [
        {"type": "user", "sessionId": "s1", "timestamp": "2026-07-17T10:00:00+00:00",
         "cwd": "/home/demo/shop", "message": {"role": "user",
         "content": f"In der config steht API_KEY = \"{fake}\". Was tun?"}},
        {"type": "assistant", "sessionId": "s1", "timestamp": "2026-07-17T10:00:05+00:00",
         "message": {"role": "assistant", "content": [{"type": "text",
         "text": f"Der Key {fake} liegt im Klartext, bitte rotieren."}]}},
    ]
    (project / "s1.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), "utf-8")
    corpus = tmp_path / "corpus"
    ingest.run("claude-code", corpus, session_root=tmp_path / "sessions",
               source_overrides_path=tmp_path / "none.json", require_source_overrides=False)
    written = "".join(p.read_text("utf-8") for p in corpus.rglob("*.md"))
    assert "Was tun?" in written
    assert fake not in written
    assert "sensitivity: nda" in written
