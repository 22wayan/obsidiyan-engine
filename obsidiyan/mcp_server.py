"""MCP-Server ueber dem Corpus. stdio, lokal, ohne Netz.

Existiert vor allem, damit Codex und Antigravity denselben Zugang bekommen wie
Claude Code, das den Corpus auch direkt greppen koennte. Der NDA-Filter sitzt
serverseitig: ein Client kann ihn nicht aus Versehen umgehen, er muss
include_nda ausdruecklich setzen.

Gebaut gegen mcp 2.0 (MCPServer, nicht das aeltere FastMCP).
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

from obsidiyan import dense, ingest, paths
from obsidiyan.corpusio import iter_docs, read_doc
from obsidiyan.models import Sensitivity
from obsidiyan.notewriter import remember as write_note
from obsidiyan.notewriter import remember_private as write_private_note
from obsidiyan.search import search as corpus_search

REPO_ROOT = paths.vault_root()
CORPUS_ROOT = paths.corpus_root()
NOTES_ROOT = paths.notes_root()
PRIVATE_ROOT = paths.private_root()
_MAX_CHARS = 8000

_DEFAULT_INSTRUCTIONS = (
    "Shared memory of past work: Claude Code and Codex sessions, chat exports from "
    "ChatGPT, Gemini and claude.ai, plus curated notes. Search here before starting "
    "a new task instead of starting from zero. Ranking prefers user statements over "
    "assistant answers and newer over older documents. Confidential documents are "
    "hidden unless include_nda=true. Store durable, general insights with remember; "
    "client or confidential knowledge goes to the local private layer with "
    "remember_private. Search before creating a note; every new note needs the path "
    "of an existing parent note."
)


def _instructions() -> str:
    """Vault-eigener Begruesungstext, sonst der Default.

    Ein zweiter Vault (Teamco) beschreibt sich anders als dieser hier. Ohne diesen
    Haken wuerden fremde Agents "Persoenliche Wissensbasis"
    lesen und danach falsch entscheiden, was hineingehoert.
    """
    override = REPO_ROOT / "mcp-instructions.md"
    if override.is_file():
        text = override.read_text(encoding="utf-8").strip()
        if text:
            return text
    return _DEFAULT_INSTRUCTIONS


server: MCPServer = MCPServer(name="obsidiyan", instructions=_instructions())


def _corpus_root() -> Path:
    """Corpus-Wurzel, aber laut statt leer, wenn sie fehlt.

    Ohne diese Pruefung sieht ein falsch aufgeloester Vault (zweite Paketkopie
    in einem Worktree, falsches OBSIDIYAN_HOME) exakt aus wie eine Wissensbasis
    ohne Treffer. Der Client hat dann keine Chance, den Unterschied zu merken.
    """
    if not CORPUS_ROOT.is_dir():
        raise paths.VaultError(
            f"No corpus under {CORPUS_ROOT}. Vault root is {REPO_ROOT}. "
            f"Point {paths.ENV_VAR} at your vault or run "
            f"'python -m obsidiyan init' and an ingest there first."
        )
    return CORPUS_ROOT


@server.tool(
    description=(
        "Search the knowledge base. Returns the best hits with date, source, snippet "
        "and doc_id. Ranking fuses BM25 with exact term matching: documents that contain "
        "every term (substring, case-insensitive, any order) come first, then the best "
        "BM25 matches, marked with matched_terms below the total. Distinctive keywords "
        "work best, natural questions work too. An exact phrase in double quotes must "
        "occur. ranking='hybrid' adds local embeddings for questions phrased in other "
        "words than the source; every hit reports the ranking actually used. "
        "Call get_doc for the full text."
    )
)
def search(
    query: str,
    limit: int = 8,
    since: str | None = None,
    source: str | None = None,
    project: str | None = None,
    include_nda: bool = False,
    ranking: str | None = None,
) -> list[dict[str, Any]]:
    wanted = ranking or os.environ.get("OBSIDIYAN_RANKING", "fused")
    if wanted not in ("fused", "hybrid", "substring"):
        raise ValueError("ranking must be fused, hybrid or substring")
    used = wanted
    if wanted == "hybrid" and not dense.available(_corpus_root()):
        used = "fused (no embedding index, run `obsidiyan embed`)"
        wanted = "fused"
    options: dict[str, Any] = {
        "limit": limit,
        "include_nda": include_nda,
        "since": date.fromisoformat(since) if since else None,
        "source": source,
        "project": project,
    }
    try:
        hits = corpus_search(query, _corpus_root(), ranking=wanted, **options)  # type: ignore[arg-type]
    except dense.EmbeddingsUnavailable as exc:
        used = f"fused ({exc})"
        hits = corpus_search(query, _corpus_root(), ranking="fused", **options)
    if not hits:
        # Auch ohne Treffer muss der Agent sehen, ob die gewuenschte Suche lief.
        return [{"note": "no results", "ranking": used}]
    return [
        {
            "doc_id": str(hit.path.relative_to(_corpus_root())),
            "date": hit.doc.started_at.date().isoformat() if hit.doc.started_at else None,
            "source": hit.doc.source.value,
            "project": hit.doc.project,
            "title": hit.doc.title,
            "sensitivity": hit.doc.sensitivity.value,
            "snippet": hit.snippet,
            "score": round(hit.score, 3),
            "matched_terms": f"{hit.matched_terms}/{hit.total_terms}",
            "ranking": used,
        }
        for hit in hits
    ]


@server.tool(
    description=(
        "Full text of one document by doc_id from search. Long documents are cut; "
        "offset moves the window."
    )
)
def get_doc(doc_id: str, offset: int = 0, include_nda: bool = False) -> dict[str, Any]:
    root = _corpus_root()
    path = (root / doc_id).resolve()
    if not path.is_file() or root.resolve() not in path.parents:
        return {"error": f"unknown doc_id: {doc_id}"}

    doc = read_doc(path)
    if doc.sensitivity is Sensitivity.NDA and not include_nda:
        return {"error": "document is confidential; call again with include_nda=true"}

    body = "\n\n".join(f"[{turn.role.value}] {turn.text}" for turn in doc.turns)
    window = body[offset : offset + _MAX_CHARS]
    return {
        "doc_id": doc_id,
        "title": doc.title,
        "source": doc.source.value,
        "project": doc.project,
        "date": doc.started_at.date().isoformat() if doc.started_at else None,
        "sensitivity": doc.sensitivity.value,
        "text": window,
        "offset": offset,
        "truncated": offset + _MAX_CHARS < len(body),
        "total_chars": len(body),
    }


@server.tool(
    description=(
        "Most recent documents, optionally filtered by source or project. Useful for "
        "'what happened lately in X' without a search term."
    )
)
def timeline(
    limit: int = 15,
    source: str | None = None,
    project: str | None = None,
    include_nda: bool = False,
) -> list[dict[str, Any]]:
    root = _corpus_root()
    rows: list[dict[str, Any]] = []
    for path in iter_docs(root):
        try:
            doc = read_doc(path)
        except (ValueError, KeyError):
            continue
        if doc.sensitivity is Sensitivity.NDA and not include_nda:
            continue
        if source and doc.source.value != source:
            continue
        if project and project.lower() not in doc.project.lower():
            continue
        rows.append(
            {
                "doc_id": str(path.relative_to(root)),
                "date": doc.started_at.date().isoformat() if doc.started_at else None,
                "source": doc.source.value,
                "project": doc.project,
                "title": doc.title,
                "turns": len(doc.turns),
            }
        )
    rows.sort(key=lambda r: r["date"] or "", reverse=True)
    return rows[:limit]


@server.tool(
    description=(
        "Store a durable insight in the committed notes layer; it is searchable right "
        "away. If the note exists, a section is appended instead of overwriting. Use it "
        "for knowledge that outlives this session: decisions with their reason, project "
        "status, preferences. Not for session logs, the ingest captures those. "
        "Confidential names, e-mail addresses and secrets are rejected. A new note needs "
        "parent, for example notes/projects; when appending, parent may stay empty."
    )
)
def remember(title: str, body: str, section: str = "", parent: str = "") -> dict[str, Any]:
    result = write_note(title, body, NOTES_ROOT, section=section, parent=parent)
    if not result.ok:
        return {"error": result.error}
    ingest.run("memory", CORPUS_ROOT, require_source_overrides=False)
    return {
        "path": str(result.path.relative_to(REPO_ROOT)),
        "created": result.created,
        "appended": result.appended,
        "note": "searchable now",
    }


@server.tool(
    description=(
        "Store client or confidential knowledge in the local, git-ignored private/ "
        "layer; it is searchable right away with include_nda=true. An existing project "
        "note is extended, not overwritten. Never store secrets, credentials or raw "
        "client data. A new note needs parent pointing to an existing node under "
        "private/, for example private/INDEX; when appending, parent may stay empty."
    )
)
def remember_private(
    project: str,
    title: str,
    body: str,
    section: str = "",
    parent: str = "",
) -> dict[str, Any]:
    result = write_private_note(
        project,
        title,
        body,
        PRIVATE_ROOT,
        section=section,
        parent=parent,
    )
    if not result.ok:
        return {"error": result.error}
    ingest.run("memory", CORPUS_ROOT, require_source_overrides=False)
    return {
        "path": str(result.path.relative_to(REPO_ROOT)),
        "created": result.created,
        "appended": result.appended,
        "sensitivity": Sensitivity.NDA.value,
        "note": "searchable now with include_nda=true",
    }


def _warm_up_embeddings() -> None:
    """Modell vorab laden, damit die erste hybride Anfrage nicht Sekunden wartet."""
    try:
        if dense.available(_corpus_root()):
            dense.encoder_for()
    except Exception:  # Warm-up ist eine Optimierung; Fehler meldet die Suche selbst
        return


def main() -> None:
    if os.environ.get("OBSIDIYAN_RANKING") == "hybrid":
        import threading

        threading.Thread(target=_warm_up_embeddings, daemon=True).start()
    server.run("stdio")


if __name__ == "__main__":
    main()
