#!/usr/bin/env python3
"""Read-only Gate fuer den kuratierten Obsidian-Graphen."""

from __future__ import annotations

from obsidiyan import paths
from obsidiyan.graph import audit_graph


def main() -> int:
    root = paths.vault_root()
    report = audit_graph(root)
    print(report.render())
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
