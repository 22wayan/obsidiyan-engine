"""Prosa-Anteil eines Textes schaetzen.

Eingefuegte Terminal-Logs, JSON-Dumps und Stacktraces sind formal Nutzertext,
denn der Mensch hat sie ins Feld kopiert. Inhaltlich sind sie Maschinenausgabe
und tragen kein persoenliches Wissen.

Ohne diese Korrektur stehen ein Manim-Installationslog und ein Shopware-API-Dump
ganz oben in der Destillations-Auswahl, weil sie zehntausende Zeichen haben.

Bewusst grob und ohne Modell: es geht um Rangfolge, nicht um Klassifikation.
"""

from __future__ import annotations

import re

_MACHINE_LINE = re.compile(
    r"""^\s*(
          [{\[\"]                    # JSON
        | \d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}   # Zeitstempel am Zeilenanfang
        | (File\ \"|Traceback|\ +at\ |\.\.\.)  # Stacktrace
        | (Collecting|Downloading|Requirement|Installing|Successfully|Using\ cached)
        | [a-zA-Z0-9_./-]+\.(py|ts|tsx|js|json|lock)\:\d+  # pfad:zeile
        | [$#>%](?:[\t\ ]|$)|\(\.?venv\)    # kurze Shell-Prompts
        | (?:                                  # volle Shell-Prompts
              PS\s+(?:[a-zA-Z]:\\|/)[^>\r\n]*>
            | [\w.-]+@[\w.-]+:(?:/|~|\.)[^$#%\r\n]*[$#%]
            | [\w.-]+@[\w-]+\s+(?:~|/[^%\r\n]*|[\w./-]+)\s+%
          )(?:[\t\ ]|$)
        | (npm|pip|yarn|pnpm|git|cd|ls|sudo)\ 
        | <(?:!DOCTYPE|/?[a-zA-Z][a-zA-Z0-9-]*(?:\s|>|/))  # HTML/XML-Dump
        | \d+/\d+\s+\[[=\->\ ]*\]\s+\d+%             # CLI-Fortschrittsbalken
        | [-=]{3,}(?:\s+[-=]{3,})+\s*$                    # CLI-Tabellenrahmen
        | Windows\s+PowerShell\s*$
        | Copyright\s+\(C\)\s+Microsoft\s+Corporation\.
          (?:\s+(?:All\s+rights\s+reserved\.|Alle\s+Rechte\s+vorbehalten\.))?\s*$
        | Last\s+login:
        | Generating\s+Thumbnails\s+for\s+\d+\s+files\.
        | (Generated|Skipped|Errors)\s+\d+\s*$
        )""",
    re.VERBOSE,
)
_SENTENCE_END = re.compile(r"[.!?:]\s*$")

# Eingefuegter Quelltext. Eigene Klasse, weil er den Log-Filter passiert:
# Code-Kommentare und String-Literale sehen wie Fliesstext aus.
_CODE_MARKERS = re.compile(
    r"(^\s*//|^\s*#(?!\s)|^\s*(def|class|function|const|let|var|import|from|return|if|for)\b"
    r"|\w+\s*=\s*\w+\(|=>|\{\s*$|^\s*\}|;\s*$|\binput\.(bool|int|float|string|color)\()",
    re.MULTILINE,
)
_CODE_LINE_SHARE = 0.25


def is_machine_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if _MACHINE_LINE.match(line):
        return True
    # Sehr lange Zeile ohne Satzende und mit vielen Sonderzeichen
    if len(stripped) > 200 and not _SENTENCE_END.search(stripped):
        symbols = sum(1 for ch in stripped if not ch.isalnum() and not ch.isspace())
        if symbols / len(stripped) > 0.12:
            return True
    return False


def is_code_block(text: str) -> bool:
    """True, wenn ein Textblock ueberwiegend Quelltext ist.

    Gemessen ueber den Anteil der Zeilen mit Code-Markern, nicht ueber einzelne
    Treffer: ein Codeschnipsel in einer Frage ist noch kein Code-Dump.
    """
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) < 8:
        return False
    hits = sum(1 for ln in lines if _CODE_MARKERS.search(ln))
    return hits / len(lines) >= _CODE_LINE_SHARE


def prose_ratio(text: str) -> float:
    """Anteil der Zeichen, die nach menschlicher Sprache aussehen.

    Zwei Stufen: erst blockweise Quelltext aussortieren, dann zeilenweise Logs.
    Ohne die erste Stufe erreicht ein Pine-Script-Dump 97 Prozent Prosa, weil
    seine Kommentarzeilen den Zeilenfilter passieren.
    """
    blocks = [b for b in re.split(r"\n\s*\n", text) if b.strip()]
    if not blocks:
        return 0.0
    total = sum(len(b) for b in blocks)
    if not total:
        return 0.0
    prose_chars = 0
    for block in blocks:
        if is_code_block(block):
            continue
        lines = [ln for ln in block.splitlines() if ln.strip()]
        prose_chars += sum(len(ln) for ln in lines if not is_machine_line(ln))
    return max(0.0, prose_chars / total)
