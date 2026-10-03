# AGENTS.md

Guide for coding agents working on this repository. Users of the engine find everything in `README.md`.

## What this is

A Python package (`obsidiyan`) that turns AI coding sessions and chat exports into a searchable Markdown corpus, distils them into sourced notes and serves both over MCP. The engine is code only. All data lives in a separate vault directory named by `OBSIDIYAN_HOME`.

## Setup and checks

```bash
uv venv && uv pip install -e ".[dev]"
scripts/check.sh            # ruff, mypy --strict, pytest; must pass before you finish
```

For a manual end-to-end run, create a throwaway vault:

```bash
export OBSIDIYAN_HOME=$(mktemp -d)/vault
.venv/bin/python -m obsidiyan init --demo
.venv/bin/python scripts/verify-mcp.py && .venv/bin/python scripts/verify-nda.py
```

Never point `OBSIDIYAN_HOME` at a real vault while testing.

## Code map

| Path | Responsibility |
|---|---|
| `obsidiyan/paths.py` | Resolves the vault from `OBSIDIYAN_HOME`; every other module asks here |
| `obsidiyan/sources/` | One adapter per source (Claude Code, Codex, provider archives, notes, courses) |
| `obsidiyan/ingest.py` | Runs an adapter, classifies, writes corpus documents, keeps the watermark |
| `obsidiyan/nda.py` | The only place that decides clean versus confidential |
| `obsidiyan/search.py` | Plain-text ranking, no index |
| `obsidiyan/notewriter.py` | `remember` and `remember_private`, with all write guards |
| `obsidiyan/distill/` | Candidate selection, claim store, routing, note emission |
| `obsidiyan/graph.py` | Note graph invariants used by `scripts/verify-graph.py` |
| `obsidiyan/mcp_server.py` | MCP tools: search, get_doc, timeline, remember, remember_private |
| `obsidiyan/vault_init.py` | `init` and the bundled demo sessions in `obsidiyan/demo/` |
| `scripts/` | Refresh, verification and the optional unattended distillation |
| `tests/` | pytest; tests write vault data only under `tmp_path` |

## Rules for changes

- **Classification stays in `nda.py`.** Do not add a second place that decides sensitivity. If you change the deny lists, change `DENY` and `DENY_TERMS` in `bin/sync-memory.sh` the same way; a test compares them.
- **Placeholders only.** Deny lists, tests, demo data and examples use invented names (Acme, Globex). Never add real client names, e-mail addresses, IDs or keys, also not in commit messages.
- **Provenance is mandatory.** A claim without `source_doc_id` must not reach a note. Do not add a path that writes notes without going through the claim store or `notewriter`.
- **The graph has invariants.** Every note except `BRAIN.md` has exactly one `parent`. `scripts/verify-graph.py` must stay green.
- **Confidential material never reaches the committed layer.** `notes/` and `memory/` are committed, `private/`, `corpus/` and `chats/` are not. `scripts/verify-nda.py` must stay green.
- **Measure search changes.** Run `scripts/eval-search.py` before and after any change to `search.py`; for larger changes also `evals/agent/run_agent_eval.py`, and report the numbers, including misses.
- **Measure classifier changes.** Run `scripts/eval-nda.py` before and after any change to `nda.py`, and add a labelled case to `evals/nda_cases.jsonl` for every bug you fix.
- **Fail loudly.** A missing vault or corpus raises `VaultError`; it must not look like an empty result.
- **No network calls** in the package. The only external process is the optional Claude Code CLI in `scripts/auto-distill.sh`.
- **Tests first for bugs.** Add a failing test that shows the bug, then fix it.

## Conventions

- Python 3.12+, type hints everywhere, mypy strict, Pydantic models for data that crosses a boundary.
- Ruff with a line length of 100.
- Code comments and docstrings are in German; user-facing CLI help and MCP tool descriptions are in English. Keep new user-facing text in English.
- Small modules with one responsibility; prefer returning new objects over mutating inputs.
