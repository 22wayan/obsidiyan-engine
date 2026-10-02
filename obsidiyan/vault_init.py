"""Create a new vault, optionally filled with demo conversations.

A vault is a plain directory. ``BRAIN.md`` is the root of the note graph, three
hub notes hang directly below it, and ``private/`` holds everything that must
never be committed. ``init`` writes exactly that skeleton, so that ``remember``
has a valid parent and ``scripts/verify-graph.py`` passes on a fresh vault.

``--demo`` copies a handful of fictional Claude Code sessions into the vault and
ingests them, so search and the MCP server have something to return right away.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from datetime import date
from importlib import resources
from pathlib import Path

from obsidiyan.ingest import run as ingest_run
from obsidiyan.models import Source

DEMO_PROJECT = "-demo-shop"
"""Folder name the demo sessions get, in the format Claude Code uses."""

HUBS: dict[str, str] = {
    "notes/profile": "Profile",
    "notes/projects": "Projects",
    "notes/knowledge": "Knowledge",
}

BRAIN_TEXT = """# Brain

Entry point of this vault. Agents read this file first.

- [[notes/profile]]: who you are and how you work
- [[notes/projects]]: what you are working on
- [[notes/knowledge]]: durable knowledge across projects
- [[private/INDEX]]: confidential layer, never committed
"""

GITIGNORE_TEXT = """# Raw and derived data stays local.
private/
corpus/
chats/
books/
courses/
distill/claims-private.json
distill/reviews-private.json
demo-sessions/
"""


@dataclass(frozen=True)
class InitResult:
    root: Path
    created: list[Path]
    demo_docs: int
    git_initialized: bool


def _note(title: str, parent: str, body: str, today: date) -> str:
    return (
        f"---\ncreated: {today.isoformat()}\nmodified: {today.isoformat()}\n"
        f'parent: "[[{parent}]]"\n---\n\n# {title}\n\n{body}\n'
    )


def _write_new(path: Path, text: str, created: list[Path]) -> None:
    """Write a file only if it does not exist yet. ``init`` never overwrites."""
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    created.append(path)


def init_vault(root: Path, *, demo: bool = False, today: date | None = None) -> InitResult:
    """Create the vault skeleton under ``root``. Existing files stay untouched."""
    stamp = today or date.today()
    root = root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []

    _write_new(root / "BRAIN.md", BRAIN_TEXT, created)
    for node, title in HUBS.items():
        _write_new(
            root / f"{node}.md",
            _note(title, "BRAIN", "Hub note. New notes on this topic use it as parent.", stamp),
            created,
        )
    _write_new(
        root / "private" / "INDEX.md",
        _note("Private index", "BRAIN", "Confidential notes live below this hub.", stamp),
        created,
    )
    _write_new(
        root / "private" / "nda-source-overrides.json",
        json.dumps({"version": 1, "sources": []}, indent=2) + "\n",
        created,
    )
    _write_new(
        root / ".obsidian" / "app.json",
        json.dumps({"userIgnoreFilters": ["memory/", "private/memory/", "corpus/"]}) + "\n",
        created,
    )
    _write_new(root / ".gitignore", GITIGNORE_TEXT, created)
    for store in ("claims.json", "reviews.json"):
        _write_new(root / "distill" / store, "[]\n", created)

    git_initialized = _ensure_git(root)
    demo_docs = _load_demo(root) if demo else 0
    # Notes are mirrored into the corpus too, so search and the NDA check see them.
    _ingest(root, Source.MEMORY.value, session_root=root)
    return InitResult(root, created, demo_docs, git_initialized)


def _ensure_git(root: Path) -> bool:
    """Make the vault a git repository, so the ignore rules for private/ apply."""
    if shutil.which("git") is None:
        return False
    inside = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if inside.returncode == 0:
        return False
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    return True


def _ingest(root: Path, source: str, *, session_root: Path) -> int:
    stats = ingest_run(
        source,
        root / "corpus",
        session_root=session_root,
        source_overrides_path=root / "private" / "nda-source-overrides.json",
        require_source_overrides=False,
    )
    return stats.written


def _load_demo(root: Path) -> int:
    """Copy the bundled demo sessions into the vault and ingest them."""
    sessions = root / "demo-sessions" / DEMO_PROJECT
    sessions.mkdir(parents=True, exist_ok=True)
    package = resources.files("obsidiyan") / "demo"
    for entry in package.iterdir():
        if entry.name.endswith(".jsonl"):
            with resources.as_file(entry) as source:
                shutil.copyfile(source, sessions / entry.name)
    return _ingest(root, Source.CLAUDE_CODE.value, session_root=root / "demo-sessions")
