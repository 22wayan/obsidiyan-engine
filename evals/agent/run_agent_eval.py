#!/usr/bin/env python3
"""Misst, ob ein Agent mit obsidiyan Fragen zur Projektgeschichte besser beantwortet.

Usage: .venv/bin/python evals/agent/run_agent_eval.py [--model haiku] [--jobs 4]

Drei Bedingungen, gleiche Fragen, gleiches Modell:

- none:      kein Zugriff auf fruehere Sessions
- raw-logs:  die rohen JSONL-Transkripte liegen im Arbeitsverzeichnis,
             der Agent darf sie mit Read, Grep und Glob durchsuchen
- obsidiyan: der Agent bekommt nur den MCP-Server (search, get_doc, timeline)

Gewertet wird automatisch gegen feste Schluesselbegriffe aus questions.json.
Gemessen werden Treffer, Tokens, Kosten laut Claude-CLI, Turns und Laufzeit.
Braucht die Claude Code CLI. Jede Sitzung laeuft mit --restricted,
--strict-mcp-config und ohne Session-Speicherung, damit weder Nutzer-Settings
noch andere MCP-Server noch Hooks das Ergebnis beeinflussen.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
EVAL = ROOT / "evals" / "agent"
RESULTS = EVAL / "results"
MAX_TURNS = "15"

BASE_PROMPT = (
    "You are a developer on Ledgerly, an invoicing SaaS. Answer the question below about "
    "decisions the team made in earlier work sessions. Answer in at most two sentences. "
    "If you cannot find the answer, reply exactly: unknown.\n\n"
)
HINTS = {
    "none": "",
    "raw-logs": (
        "The raw transcripts of the team's past sessions are JSONL files below the current "
        "directory.\n\n"
    ),
    "obsidiyan": (
        "The team's past sessions are searchable through the obsidiyan tools "
        "(search, then get_doc).\n\n"
    ),
}
TOOLS = {
    "none": ("", ""),
    "raw-logs": ("Read,Grep,Glob", "Read Grep Glob"),
    "obsidiyan": ("", "mcp__obsidiyan__search mcp__obsidiyan__get_doc mcp__obsidiyan__timeline"),
}


def build_vault(base: Path, history: Path, ranking: str) -> Path:
    vault = base / "vault"
    vault.mkdir(parents=True)
    os.environ["OBSIDIYAN_HOME"] = str(vault)
    sys.path.insert(0, str(ROOT))
    from obsidiyan.ingest import run as ingest_run
    from obsidiyan.vault_init import init_vault

    init_vault(vault, today=date(2026, 7, 20))
    ingest_run(
        "claude-code",
        vault / "corpus",
        session_root=history,
        source_overrides_path=vault / "private" / "nda-source-overrides.json",
        require_source_overrides=False,
    )
    if ranking == "hybrid":
        from obsidiyan import dense

        dense.update_index(vault / "corpus", dense.sentence_transformer_encoder())
    return vault


def grade(answer: str, must: list[list[str]]) -> bool:
    text = answer.lower()
    return all(any(alt in text for alt in group) for group in must)


def run_one(
    condition: str, question: dict[str, Any], model: str, dirs: dict[str, Path], mcp: Path
) -> dict[str, Any]:
    tools, allowed = TOOLS[condition]
    cmd = [
        "claude", "-p", BASE_PROMPT + HINTS[condition] + "Question: " + question["question"],
        "--model", model, "--output-format", "json", "--no-session-persistence",
        "--restricted", "--strict-mcp-config", "--max-turns", MAX_TURNS, "--tools", tools,
    ]
    if condition == "obsidiyan":
        cmd += ["--mcp-config", str(mcp)]
    if allowed:
        cmd += ["--allowedTools", *allowed.split()]
    started = time.monotonic()
    proc = subprocess.run(
        cmd, cwd=dirs[condition], capture_output=True, text=True, timeout=600, check=False
    )
    seconds = time.monotonic() - started
    try:
        out = json.loads(proc.stdout)
    except json.JSONDecodeError:
        out = {"result": "", "is_error": True, "error": proc.stderr[-300:]}
    usage = out.get("usage") or {}
    answer = str(out.get("result") or "")
    return {
        "condition": condition,
        "id": question["id"],
        "category": question["category"],
        "correct": grade(answer, question["must"]),
        "answer": answer.strip()[:400],
        "error": bool(out.get("is_error")),
        "turns": out.get("num_turns"),
        "cost_usd": out.get("total_cost_usd"),
        "input_tokens": sum(
            int(usage.get(k) or 0)
            for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
        ),
        "output_tokens": int(usage.get("output_tokens") or 0),
        "seconds": round(seconds, 1),
    }


def summarise(rows: list[dict[str, Any]], conditions: list[str]) -> str:
    lines = [
        "| Condition | Correct | Avg input tokens | Avg cost (USD) | Avg turns | Avg seconds |",
        "|---|---|---|---|---|---|",
    ]
    for c in conditions:
        rs = [r for r in rows if r["condition"] == c]
        n = len(rs)

        def avg(key: str, rows_: list[dict[str, Any]] = rs) -> float:
            vals = [float(r[key]) for r in rows_ if r[key] is not None]
            return sum(vals) / len(vals) if vals else 0.0

        correct = sum(r["correct"] for r in rs)
        lines.append(
            f"| {c} | {correct}/{n} | {avg('input_tokens'):,.0f} | {avg('cost_usd'):.4f} "
            f"| {avg('turns'):.1f} | {avg('seconds'):.1f} |"
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="haiku")
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--conditions", default="none,raw-logs,obsidiyan")
    parser.add_argument("--limit", type=int, default=None, help="only the first N questions")
    parser.add_argument("--history", choices=["clean", "realistic"], default="clean")
    parser.add_argument("--ranking", choices=["fused", "hybrid", "substring"], default="fused")
    parser.add_argument("--run", default="", help="suffix for the result files, e.g. run1")
    args = parser.parse_args()
    if shutil.which("claude") is None:
        print("claude CLI not found", file=sys.stderr)
        return 2

    base = Path(tempfile.mkdtemp(prefix="obsidiyan-agent-eval-"))
    history = EVAL / ("history" if args.history == "clean" else "history-realistic")
    if not history.is_dir():
        print(f"{history} missing, run build_history.py --realistic first", file=sys.stderr)
        return 2
    vault = build_vault(base, history, args.ranking)
    dirs = {c: base / c for c in ("none", "raw-logs", "obsidiyan")}
    for d in dirs.values():
        d.mkdir()
    shutil.copytree(history, dirs["raw-logs"] / "sessions")
    mcp = base / "mcp.json"
    mcp.write_text(json.dumps({"mcpServers": {"obsidiyan": {
        "command": sys.executable,
        "args": ["-m", "obsidiyan.mcp_server"],
        "env": {"OBSIDIYAN_HOME": str(vault), "OBSIDIYAN_RANKING": args.ranking},
    }}}), encoding="utf-8")

    questions = json.loads((EVAL / "questions.json").read_text(encoding="utf-8"))[: args.limit]
    conditions = args.conditions.split(",")
    jobs = [(c, q) for c in conditions for q in questions]
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        rows = list(pool.map(lambda job: run_one(job[0], job[1], args.model, dirs, mcp), jobs))

    RESULTS.mkdir(exist_ok=True)
    suffix = f"-{args.run}" if args.run else ""
    stamp = f"{date.today().isoformat()}-{args.history}-{args.model}-{args.ranking}{suffix}"
    (RESULTS / f"{stamp}.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    table = summarise(rows, conditions)
    (RESULTS / f"{stamp}.md").write_text(
        f"# Agent eval {stamp}\n\n{len(questions)} questions, history {args.history}, "
        f"model {args.model}, ranking {args.ranking}.\n\n{table}\n",
        encoding="utf-8",
    )
    print(table)
    errors = [r for r in rows if r["error"]]
    if errors:
        print(f"\n{len(errors)} runs ended with an error", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
