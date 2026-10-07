"""UserPromptSubmit-Hook: holt passende Erinnerungen, bevor der Agent antwortet.

Agents rufen das Gedaechtnis selten von sich aus auf. Auf 1.250 echten Prompts
kam der MCP-Aufruf nur in 11 von 60 Faellen, in denen fruehere Sessions klar
gebraucht wurden. Dieser Hook erkennt solche Prompts (Erinnerungs-Hinweise oder
bekannte Projektnamen), sucht selbst per BM25 und legt die besten Treffer als
Kontext vor den Prompt. Der Agent entscheidet weiter selbst, ob er tiefer liest.

Einbindung in Claude Code (settings.json):
    "hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command",
        "command": "OBSIDIYAN_HOME=~/vault /pfad/.venv/bin/python -m obsidiyan.hook"}]}]}

Der Hook blockiert nie: jeder Fehler endet still mit Exit 0. Vertrauliche
Treffer werden nur gezaehlt, nie gezeigt.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from obsidiyan import paths
from obsidiyan.models import Sensitivity

_MIN_CHARS = 15
_SHORT_REPLY_CHARS = 60
_MAX_HITS = 3
_SNIPPET_CHARS = 220
_AUTOMATION = ("<!-- obsidiyan-automation", "Reply with exactly", "Sponsored synthetic task")
_SHORT_REPLY = re.compile(
    r"^\s*(ja|jo|nein|ok|okay|mach|mache|los|weiter|go|merge|passt|gut|danke|yes|no|sure)\b",
    re.IGNORECASE,
)
_CUES = re.compile(
    r"weißt du noch|weisst du noch|erinnerst du dich|kannst du dich (noch )?erinnern"
    r"|wie hatten wir|(hatten|haben) wir (doch|schon|nicht|damals|letztens)"
    r"|letztes mal|damals|neulich|(im|ins|aus dem|in meinem) brain"
    r"|(was|wie) war (das|nochmal|noch mal)|wo waren wir|wie ist der stand|stand (von|bei)"
    r"|\bremember\b|last time|did we\b|as we discussed",
    re.IGNORECASE,
)
# Dateinamen, die Themen statt Projekte benennen, loesen nichts aus. Die Liste ist bewusst
# kurz: eine laengere (ohne codex, trading, nda, ...) senkte die Trefferquote bei klar
# gedaechtnisbeduerftigen Prompts von 60 auf 40 Prozent.
_GENERIC_TEXT = """
    index memory karriere studium projektportfolio selbststaendigkeit eval protokoll berichte
    persoenlicher herkunft agent geteilte unternehmens vault portable
"""
_GENERIC = frozenset(_GENERIC_TEXT.split())


@dataclass(frozen=True)
class Decision:
    fire: bool
    reason: str
    entity: str = ""


def known_entities(vault: Path) -> set[str]:
    """Projekt- und Organisationsnamen aus den Note-Dateinamen (erstes Wort, ab 3 Zeichen)."""
    names: set[str] = set()
    for folder in (vault / "notes", vault / "private"):
        if not folder.is_dir():
            continue
        for entry in folder.iterdir():
            stem = entry.stem.lower()
            if stem.startswith(("_", ".")):
                continue
            head = stem.split("-")[0]
            if len(head) >= 3 and head not in _GENERIC and not head.isdigit():
                names.add(head)
    return names


def decide(prompt: str, entities: set[str], current_repo: str = "") -> Decision:
    # Conductor stellt einen Systemblock voran; entschieden wird nur ueber den Nutzertext.
    text = re.sub(r"(?s)<system_instruction>.*?</system_instruction>", "", prompt).strip()
    if text.startswith(_AUTOMATION):
        return Decision(False, "automation")
    if len(text) < _MIN_CHARS:
        return Decision(False, "too short")
    if len(text) < _SHORT_REPLY_CHARS and _SHORT_REPLY.match(text):
        return Decision(False, "short reply")
    lowered = text.lower()
    repo = current_repo.lower()
    entity = next(
        (
            name
            for name in sorted(entities, key=len, reverse=True)
            if name not in repo
            and re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", lowered)
        ),
        "",
    )
    if _CUES.search(text):
        return Decision(True, "recall cue", entity)
    if entity:
        return Decision(True, f"mentions {entity}", entity)
    return Decision(False, "no cue")


def _repo_name(cwd: str) -> str:
    return Path(cwd).name if cwd else ""


def build_query(prompt: str, entity: str = "") -> str:
    """Suchanfrage aus dem Prompt: Systemblock und Erinnerungsfloskeln raus, Projektname Pflicht.

    "weisst du noch, wie wir bei X die Preise festgelegt haben" sucht sonst nach den Floskeln
    und findet beliebige Chats ueber Preise.
    """
    text = re.sub(r"(?s)<system_instruction>.*?</system_instruction>|<[^>]+>", " ", prompt)
    text = _CUES.sub(" ", text)[:500]
    if entity:
        text = re.sub(rf"(?i)(?<![\w-]){re.escape(entity)}(?![\w-])", " ", text)
        return f'"{entity}" {text}'.strip()
    return text.strip()


def context_for(prompt: str, corpus: Path, entity: str = "") -> str:
    from obsidiyan.search import search

    query = build_query(prompt, entity)
    hits = search(query, corpus, limit=12, include_nda=True)
    shown = [hit for hit in hits if hit.doc.sensitivity is not Sensitivity.NDA][:_MAX_HITS]
    hidden = sum(hit.doc.sensitivity is Sensitivity.NDA for hit in hits[: _MAX_HITS * 2])
    if not shown and hidden <= 0:
        return ""
    lines = ["Obsidiyan (Gedaechtnis aus frueheren Sessions), passende Treffer zu diesem Prompt:"]
    for hit in shown:
        day = hit.doc.started_at.date().isoformat() if hit.doc.started_at else "?"
        doc_id = hit.path.relative_to(corpus).as_posix()
        snippet = hit.snippet[:_SNIPPET_CHARS]
        lines.append(f"- {day} {hit.doc.title or doc_id}: {snippet} (doc_id {doc_id})")
    if hidden > 0:
        lines.append(
            f"- {hidden} weitere Treffer sind vertraulich; bei Kunden- oder DHC-Arbeit mit "
            "include_nda=true suchen."
        )
    lines.append(
        "Nur verwenden, wenn es zur Aufgabe passt; Details mit mcp__obsidiyan__get_doc lesen."
    )
    return "\n".join(lines)


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        prompt = str(payload.get("prompt", ""))
        vault = paths.vault_root()
        decision = decide(prompt, known_entities(vault), _repo_name(str(payload.get("cwd", ""))))
        if not decision.fire:
            return 0
        context = context_for(prompt, paths.corpus_root(), decision.entity)
        if context:
            print(
                json.dumps(
                    {
                        "hookSpecificOutput": {
                            "hookEventName": "UserPromptSubmit",
                            "additionalContext": context,
                        }
                    },
                    ensure_ascii=False,
                )
            )
    except Exception:  # ein Hook darf den Prompt nie blockieren
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
