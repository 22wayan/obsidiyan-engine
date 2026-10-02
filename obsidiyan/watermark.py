"""Inkrementeller Ingest.

Bewusst NICHT nur mtime: Claude Code schreibt waehrend einer laufenden Session an
die JSONL an. Eine reine mtime-Marke wuerde eine halb geschriebene Session als
erledigt abhaken. Deshalb (size, mtime_ns, ctime_ns); aendert sich eins davon,
wird die Datei neu gelesen. Nanosekunden plus Metadaten-Aenderungszeit verhindern
den realen Same-size-Same-second-Miss der frueheren Ganzsekunden-Marke, auch wenn
ein Tool die mtime wiederherstellt.
"""

from __future__ import annotations

import json
from pathlib import Path

Fingerprint = tuple[int, int, int]
_FORMAT_VERSION = 3


class Watermark:
    def __init__(self, path: Path, *, policy_version: str = "1") -> None:
        self.path = path
        self.policy_version = policy_version
        self._seen: dict[str, list[int]] = {}
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            if (
                isinstance(raw, dict)
                and raw.get("format_version") == _FORMAT_VERSION
                and raw.get("policy_version") == policy_version
                and isinstance(raw.get("files"), dict)
            ):
                self._seen = {str(k): list(v) for k, v in raw["files"].items()}

    @staticmethod
    def fingerprint(file: Path) -> Fingerprint:
        stat = file.stat()
        return (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)

    def is_fresh(self, file: Path) -> bool:
        """True, wenn die Datei seit dem letzten Lauf neu oder veraendert ist."""
        return self._seen.get(str(file)) != list(self.fingerprint(file))

    def mark(self, file: Path) -> None:
        self._seen[str(file)] = list(self.fingerprint(file))

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "format_version": _FORMAT_VERSION,
            "policy_version": self.policy_version,
            "files": self._seen,
        }
        self.path.write_text(json.dumps(payload, indent=0, sort_keys=True), encoding="utf-8")
