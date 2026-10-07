#!/usr/bin/env python
"""Read-only End-to-End-Pruefung des MCP-Servers ueber echtes stdio.

Startet den Server als Subprozess, macht den Handshake, ruft die Read-Tools auf
und prueft die NDA-Grenze am lebenden Objekt. Providerunabhaengig: wenn das hier
gruen ist, funktioniert jeder MCP-faehige Client, der den Server startet.

Exit 0 nur wenn alles haelt.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

REPO_ROOT = Path(__file__).resolve().parent.parent
PYTHON = REPO_ROOT / ".venv" / "bin" / "python"


def rows(result: Any) -> list[dict[str, Any]]:
    """MCP 2.0 verpackt Listen als structured_content['result']."""
    structured = getattr(result, "structured_content", None)
    if isinstance(structured, dict) and isinstance(structured.get("result"), list):
        return [r for r in structured["result"] if isinstance(r, dict)]
    return [json.loads(block.text) for block in result.content]


# Defaults match the demo vault from `obsidiyan init --demo`. For your own vault,
# pass a term that exists in a clean document and one that only appears in a
# confidential document: verify-mcp.py <clean-term> <confidential-term>
SEARCH_PROBE = sys.argv[1] if len(sys.argv) > 1 else "Postgres"
PRIVATE_PROBE = sys.argv[2] if len(sys.argv) > 2 else "Acme"


async def run() -> int:
    params = StdioServerParameters(
        command=str(PYTHON),
        args=["-m", "obsidiyan.mcp_server"],
        cwd=str(REPO_ROOT),
        env=dict(os.environ),
    )
    async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as session:
        await session.initialize()

        names = {tool.name for tool in (await session.list_tools()).tools}
        if not {"search", "get_doc", "timeline", "remember", "remember_private"} <= names:
            print(f"FAIL: tools missing, found {sorted(names)}", file=sys.stderr)
            return 1
        print(f"tools: {sorted(names)}")

        hits = rows(await session.call_tool("search", {"query": SEARCH_PROBE, "limit": 3}))
        if not hits:
            print("FAIL: search returned nothing, is the corpus empty?", file=sys.stderr)
            return 1
        for hit in hits:
            print(f"  {hit['date']}  {hit['source']:12} {hit['title'][:46]}")

        doc = (await session.call_tool("get_doc", {"doc_id": hits[0]["doc_id"]})).structured_content
        if not isinstance(doc, dict) or not doc.get("text"):
            print("FAIL: get_doc returned no text", file=sys.stderr)
            return 1
        print(f"  get_doc ok, {doc['total_chars']} characters")

        private_probe = PRIVATE_PROBE
        # Ohne Treffer liefert search eine Hinweiszeile ohne doc_id; nur echte Treffer zaehlen.
        probe = {"query": private_probe, "limit": 10}
        leaks = [hit for hit in rows(await session.call_tool("search", probe)) if "doc_id" in hit]
        if leaks:
            print("FAIL: confidential hit returned without include_nda", file=sys.stderr)
            return 1
        scoped = rows(
            await session.call_tool(
                "search",
                {"query": private_probe, "limit": 10, "include_nda": True},
            )
        )
        if not scoped or any(hit.get("sensitivity") != "nda" for hit in scoped):
            print("FAIL: confidential hit missing or not marked nda", file=sys.stderr)
            return 1
        print("  NDA boundary holds: no confidential document without include_nda")

        traversal = (await session.call_tool(
            "get_doc", {"doc_id": "../../etc/passwd"}
        )).structured_content
        if not (isinstance(traversal, dict) and traversal.get("error")):
            print("FAIL: path traversal not rejected", file=sys.stderr)
            return 1
        print("  path traversal rejected")

        timeline_rows = rows(await session.call_tool("timeline", {"limit": 3}))
        print(f"  timeline: {[row['date'] for row in timeline_rows]}")
    print("PASS: MCP server works end to end")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
