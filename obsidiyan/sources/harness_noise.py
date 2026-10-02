"""Harness-Artefakte in user-Turns erkennen und behandeln.

Ein user-Turn im Transkript ist nicht automatisch etwas, das der Mensch getippt
hat. Der Harness spielt dort ein:

  reines Maschinenrauschen  -> loeschen
    <task-notification>, <system-reminder>, command-wrapper, command-stdout
  maschinengeschriebener Inhalt -> als assistant fuehren, nicht als user
    Compaction-Zusammenfassungen ("This session is being continued ...")

Der zweite Fall ist der wichtige. Die Zusammenfassungen tragen echten Inhalt und
sollen auffindbar bleiben, aber sie sind NICHT des Nutzers Aussage. Wuerden sie als
user gefuehrt, schriebe die Destillation ihm Saetze zu, die ein Modell verfasst
hat, und das Ranking wuerde sie als autoritative Personenquelle hochgewichten.
"""

from __future__ import annotations

import re

_NOISE_BLOCK_RE = re.compile(
    r"<(system-reminder|command-message|command-args|local-command-stdout"
    r"|local-command-stderr|task-notification)>.*?</\1>",
    re.DOTALL | re.IGNORECASE,
)
_COMMAND_NAME_RE = re.compile(r"<command-name>(.*?)</command-name>", re.DOTALL | re.IGNORECASE)

# Rollenmarker einer eingefuegten Unterhaltung. Wenn im Nutzer-Feld beide Seiten
# eines Dialogs stehen, hat der Mensch ein Transkript hineinkopiert. Der Inhalt
# ist echt, aber er ist nicht seine Aussage.
_PASTED_DIALOG = re.compile(
    r"^\s*(ChatGPT|Du|Assistant|Gemini|Claude|User|Nutzer)\s*:\s*$",
    re.MULTILINE | re.IGNORECASE,
)
_PASTED_DIALOG_MIN_MARKERS = 2

# Vom Harness eingespielte Zusammenfassungen einer vorherigen Session.
_MACHINE_AUTHORED = (
    re.compile(r"^\s*This session is being continued from a previous conversation", re.IGNORECASE),
    re.compile(r"^\s*<summary>", re.IGNORECASE),
    re.compile(r"^\s*\[external unsupported block", re.IGNORECASE),
)


def strip_noise(text: str) -> str:
    """Reines Maschinenrauschen entfernen. Command-Namen bleiben, sie sind ein Signal."""
    text = _NOISE_BLOCK_RE.sub("", text)
    text = _COMMAND_NAME_RE.sub(lambda m: m.group(1).strip(), text)
    return text.strip()


def is_pasted_dialog(text: str) -> bool:
    """True, wenn im Nutzer-Feld ein ganzes Gespraech eingefuegt wurde.

    Ein einzelner Marker kann Zitat sein, zwei oder mehr bedeuten Transkript.
    """
    return len(_PASTED_DIALOG.findall(text)) >= _PASTED_DIALOG_MIN_MARKERS


def is_machine_authored(text: str) -> bool:
    """True, wenn der Text nicht die eigene Aussage des Menschen ist."""
    return any(rx.match(text) for rx in _MACHINE_AUTHORED) or is_pasted_dialog(text)
