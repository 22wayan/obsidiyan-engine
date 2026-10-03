"""NDA-Klassifikation, Default-Deny.

Regel, bewusst maschinell entscheidbar statt "im Zweifel":
Ein Doc ist NDA, wenn der Projekt-Slug auf der Deny-Liste steht ODER der Text auf
die Entity-Regex matcht. Sonst clean.

Die Deny-Liste ist dieselbe wie in bin/sync-memory.sh, damit es nur eine Wahrheit
gibt. Wenn dort etwas dazukommt, muss es hier auch dazu.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Collection
from pathlib import Path

from obsidiyan import paths
from obsidiyan.models import Sensitivity, Source

SourceKey = tuple[str, str]
DEFAULT_SOURCE_OVERRIDES = paths.source_overrides_path()

# Beispielwerte. Echte Kunden- und Projektnamen gehoeren in eine lokale,
# nicht versionierte Kopie dieser Listen, sonst verraet schon der Code die Kunden.
#
# Projekt-Slugs, deren Material nie in den git-getrackten Clean-Layer darf.
# Gespiegelt aus bin/sync-memory.sh (DENY).
DENY_SLUGS: tuple[str, ...] = (
    "acme-poc",
    "CRM-globex",
    "CRM_globex",
    "interviewkit",
    "Teamco-Website",
    "ini-tech",
    "Umbrella-Energie",
    "hooli-mag-website",
    "Globex-GmbH",
    "final-pr-acme",
    "acme-tooling",
)

# Entities, die NDA-Material im Freitext verraten. Wortgrenzen, damit
# "Acme" matcht, "Acmeware" aber nicht.
DENY_TERMS: tuple[str, ...] = (
    "acme",
    "globex",
    "docparse",
    "interviewkit",
    "storybot",
    "ini.tech",
    "ini-tech",
    "ini tech",
    "Umbrella Energie",
    "Umbrella-Energie",
    "hooli mag",
    "hooli-mag",
    "Teamco Website",
    "Teamco-Website",
)

_TERM_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(term) for term in DENY_TERMS) + r")\b",
    re.IGNORECASE,
)
# "@2x." ist die uebliche Benennung von Retina-Grafiken (logo@2x.png), keine Adresse.
EMAIL_RE = re.compile(r"[A-Z0-9._%+~-]+@(?![0-9]x\.)[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)
SECRET_RE = re.compile(
    r"(?:"
    # Nur am Wortanfang: "task-runner-..." enthaelt "sk-" und ist kein Key.
    r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{20,}|"
    r"github_pat_[A-Za-z0-9_]{20,}|"
    r"gh[pousr]_[A-Za-z0-9]{20,}|"
    r"AKIA[0-9A-Z]{16}|"
    r"AIza[0-9A-Za-z_-]{35}|"
    r"an_sk_[A-Za-z0-9_-]{20,}|"
    r"sk_(?:live|test)_[A-Za-z0-9]{16,}|"
    r"xox[abpr]-[A-Za-z0-9-]{10,}|"
    r"glpat-[A-Za-z0-9_-]{20,}|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    # Zuweisung an einen Key-Namen mit langem Wert aus Buchstaben UND Ziffern.
    # Die Ziffern-Bedingung laesst Verweise wie process.env.ANTHROPIC_API_KEY
    # durch; ein unbekanntes Key-Format faellt trotzdem auf.
    r"(?i:[A-Z0-9_]*(?:API_?KEY|SECRET|TOKEN|PASSWORD|PASSWD))\b[\"']?\s*[=:]\s*[\"']?"
    r"(?=[A-Za-z0-9_\-./+]*\d)(?=[A-Za-z0-9_\-./+]*[A-Za-z])[A-Za-z0-9_\-./+]{24,}"
    r")"
)
REDACTED = "[secret removed]"
_SLUG_RES = tuple(re.compile(re.escape(s), re.IGNORECASE) for s in DENY_SLUGS)


def slug_is_denied(slug: str) -> bool:
    """True, wenn der Projekt-Slug auf der Deny-Liste steht (Substring, case-insensitiv)."""
    return any(r.search(slug) for r in _SLUG_RES)


def text_is_denied(text: str) -> bool:
    """True, wenn der Freitext eine NDA-Entity enthaelt."""
    return _TERM_RE.search(text) is not None


def text_has_email(text: str) -> bool:
    """E-Mail-Adressen sind privat und duerfen nicht in den Clean-Layer."""
    return EMAIL_RE.search(text) is not None


def text_has_secret(text: str) -> bool:
    """Konservative Erkennung gaengiger echter Token- und Key-Formate."""
    return SECRET_RE.search(text) is not None


def redact_secrets(text: str) -> str:
    """Ersetzt erkannte Keys und Tokens, bevor Text in den Corpus geschrieben wird.

    Die Einstufung als NDA allein reicht nicht: eine Suche mit include_nda
    liefert den Text sonst samt Key an den Agent.
    """
    return SECRET_RE.sub(REDACTED, text)


def classify(project: str, text: str) -> Sensitivity:
    """Die einzige Stelle, an der ueber NDA entschieden wird."""
    combined = f"{project}\n{text}"
    if (
        slug_is_denied(project)
        or text_is_denied(combined)
        or text_has_email(combined)
        or text_has_secret(combined)
    ):
        return Sensitivity.NDA
    return Sensitivity.CLEAN


def load_source_overrides(
    path: Path = DEFAULT_SOURCE_OVERRIDES,
    *,
    required: bool = True,
) -> frozenset[SourceKey]:
    """Liest lokale Privacy-Entscheidungen, ohne vertrauliche Begriffe zu versionieren.

    Provider-Archive tragen oft nur einen generischen Projekt-Slug. Ein einzelner
    Chat kann trotzdem Kunden-, Gesundheits- oder NDA-Kontext enthalten, ohne dass
    eine stabile Entity im Text vorkommt. Solche Ausnahmen werden lokal ueber die
    unveraenderliche Kombination aus Provider und Conversation-ID klassifiziert.
    """
    if not path.exists():
        if required:
            raise FileNotFoundError(
                f"NDA source overrides missing: {path}. Run 'obsidiyan init' or "
                "restore the file from your backup."
            )
        return frozenset()
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise ValueError("nda-source-overrides.json braucht version=1")
    rows = raw.get("sources")
    if not isinstance(rows, list):
        raise ValueError("nda-source-overrides.json braucht eine sources-Liste")

    out: set[SourceKey] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("jeder Source-Override muss ein Objekt sein")
        source = row.get("source")
        conv_id = row.get("conv_id")
        if not isinstance(source, str) or not source.strip():
            raise ValueError("Source-Override braucht source")
        if source.strip() not in {item.value for item in Source}:
            raise ValueError(f"Source-Override hat unbekannte source: {source!r}")
        if not isinstance(conv_id, str) or not conv_id.strip():
            raise ValueError("Source-Override braucht conv_id")
        out.add((source.strip(), conv_id.strip()))
    if required and not out:
        raise ValueError(
            "NDA source overrides are empty; restore the file from your backup."
        )
    return frozenset(out)


def source_is_denied(
    source: str,
    conv_id: str,
    overrides: Collection[SourceKey],
) -> bool:
    """True fuer eine lokal als privat eingestufte Provider-Konversation."""
    return (source, conv_id) in overrides


def policy_signature(source_overrides: Collection[SourceKey] = ()) -> str:
    """Aendert sich die Deny-Policy, muss der inkrementelle Ingest neu klassifizieren."""
    override_rows = [f"{source}\0{conv_id}" for source, conv_id in sorted(source_overrides)]
    payload = "\0".join(
        (
            *DENY_SLUGS,
            "--terms--",
            *DENY_TERMS,
            EMAIL_RE.pattern,
            SECRET_RE.pattern,
            "--source-overrides--",
            *override_rows,
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _sync_memory_array(repo_root: Path, name: str) -> tuple[str, ...]:
    """Liest ein Bash-Array aus sync-memory.sh fuer die Drift-Tests."""
    script = repo_root / "bin" / "sync-memory.sh"
    body = script.read_text(encoding="utf-8")
    block = re.search(rf"^{re.escape(name)}=\((.*?)^\)", body, re.MULTILINE | re.DOTALL)
    if block is None:
        return ()
    return tuple(re.findall(r'"([^"]+)"', block.group(1)))


def sync_memory_deny_list(repo_root: Path) -> tuple[str, ...]:
    """Liest die DENY-Liste aus bin/sync-memory.sh, um Drift sichtbar zu machen.

    Nur fuer den Drift-Test gedacht, nicht fuer den Hot Path.
    """
    return _sync_memory_array(repo_root, "DENY")


def sync_memory_deny_terms(repo_root: Path) -> tuple[str, ...]:
    """Liest die Entity-Liste des Memory-Syncs fuer den Drift-Test."""
    return _sync_memory_array(repo_root, "DENY_TERMS")
