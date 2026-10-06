"""Suche ueber dem Corpus. Kein Index, ripgrep plus Ranking.

Query-Semantik: die Query wird an Whitespace in Terme zerlegt, ein Dokument
muss JEDEN Term enthalten (UND, Teilstring, case-insensitive, Reihenfolge
egal). Eine exakte Wortfolge haelt man mit doppelten Anfuehrungszeichen
zusammen. Alle Terme sind literal, nie Regex. Eval-Befund 2026-08-09: die
fruehere Exakte-Phrasen-Semantik liess realistische Mehrwort-Queries mit
null Treffern ins Leere laufen.

Das Ranking bildet die Vorrangregel aus BRAIN.md ab: die neueste datierte
NUTZER-Aussage gewinnt, Assistant-Antworten sind keine autoritative
Personenquelle. Deshalb Recency-Boost plus Rollen-Gewicht.

NDA-Docs sind per Default unsichtbar. Der Filter sitzt hier, nicht beim Aufrufer.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Literal

from obsidiyan import bm25
from obsidiyan.corpusio import read_doc
from obsidiyan.models import Doc, Role, Sensitivity

_USER_WEIGHT = 2.0
_MACHINE_SUMMARY_WEIGHT = 1.5  # protokolliert Nutzer-Entscheidungen, aber nicht woertlich
_ASSISTANT_WEIGHT = 1.0
_TITLE_WEIGHT = 2.0  # Session-Titel benennen oft das Thema, das die Turns nicht wiederholen
_RECENCY_HALFLIFE_DAYS = 120.0
_BM25_DEPTH = 100  # RRF-Beitraege jenseits von Rang 100 sind kleiner als 1/160
_RRF_K = 60  # Standardwert aus Cormack et al. 2009, braucht keine gelabelten Daten

Ranking = Literal["fused", "substring"]


@dataclass(frozen=True)
class Hit:
    path: Path
    doc: Doc
    score: float
    snippet: str
    matched_terms: int = 0
    total_terms: int = 0

    def render(self) -> str:
        day = self.doc.started_at.date().isoformat() if self.doc.started_at else "????-??-??"
        level = self.doc.sensitivity
        flag = "" if level is Sensitivity.CLEAN else f" [{level.value.upper()}]"
        title = self.doc.title or self.doc.project or self.doc.conv_id[:12]
        head = f"{day}  {self.doc.source.value:12} {title[:52]:52}{flag}"
        if self.matched_terms < self.total_terms:
            head += f"  (matches {self.matched_terms} of {self.total_terms} terms)"
        return f"{head}\n    {self.snippet}\n    {self.path}"


def _ripgrep_binary() -> str | None:
    """Nur ein echtes Binary zaehlt. Auf dieser Maschine ist `rg` eine Shell-Funktion,
    die in einem subprocess nicht existiert, deshalb wird explizit geprueft."""
    return shutil.which("rg")


_TOKEN = re.compile(r'"([^"]+)"|(\S+)')


def _parse_query(query: str) -> list[str]:
    """Zerlegt die Query in UND-verknuepfte Literal-Terme.

    Doppelte Anfuehrungszeichen halten eine Wortfolge als einen Term zusammen;
    unbalancierte Anfuehrungszeichen werden vom Einzelwort gestreift.
    """
    terms: list[str] = []
    for quoted, bare in _TOKEN.findall(query):
        term = quoted if quoted else bare.strip('"')
        if term:
            terms.append(term)
    return terms


def _files_matching_term(term: str, corpus_root: Path, binary: str) -> set[Path]:
    # Term per -e uebergeben, sonst liest rg einen Term mit fuehrendem
    # Bindestrich (z.B. "--include-nda") als Flag und bricht ab.
    proc = subprocess.run(
        [
            binary,
            "--files-with-matches",
            "--ignore-case",
            "--fixed-strings",
            "--glob",
            "*.md",
            "-e",
            term,
            ".",
        ],
        cwd=corpus_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode not in (0, 1):
        raise RuntimeError(f"ripgrep fehlgeschlagen: {proc.stderr.strip()}")
    return {corpus_root / line for line in proc.stdout.splitlines() if line}


def _candidate_files(terms: list[str], corpus_root: Path) -> list[Path]:
    """Vorauswahl der Dateien, die ALLE Terme enthalten.

    ripgrep wenn verfuegbar (ein Lauf pro Term, Schnittmenge), sonst Python in
    einem Durchgang pro Datei. Bei Corpus-Groessen in dieser Groessenordnung ist
    der Unterschied nicht spuerbar, und der Fallback macht die Suche unabhaengig
    von einer Systeminstallation.
    """
    if not corpus_root.exists() or not terms:
        return []

    binary = _ripgrep_binary()
    if binary is not None:
        result: set[Path] | None = None
        for term in terms:
            matched = _files_matching_term(term, corpus_root, binary)
            result = matched if result is None else result & matched
            if not result:
                return []
        return sorted(result or [])

    # lower() statt casefold(): rg --ignore-case nutzt Unicode simple case
    # folding, casefold() waere full folding (ß→ss) und liesse die Treffer
    # davon abhaengen, ob auf der Maschine ein rg-Binary liegt.
    needles = [term.lower() for term in terms]
    candidates: list[Path] = []
    for path in corpus_root.rglob("*.md"):
        text = path.read_text(encoding="utf-8", errors="replace").lower()
        if all(needle in text for needle in needles):
            candidates.append(path)
    return candidates


_DOC_CACHE: dict[Path, tuple[int, Doc]] = {}
_DOC_CACHE_SIZE = 4096


def _read_cached(path: Path) -> Doc | None:
    """read_doc mit Prozess-Cache, gueltig solange die Datei unveraendert ist.

    Der MCP-Server beantwortet viele Anfragen hintereinander; das YAML-Parsen
    derselben Dokumente war der groesste Einzelposten einer Suche.
    """
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        return None
    cached = _DOC_CACHE.get(path)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    try:
        doc = read_doc(path)
    except (ValueError, KeyError, OSError):
        return None
    if len(_DOC_CACHE) >= _DOC_CACHE_SIZE:
        _DOC_CACHE.pop(next(iter(_DOC_CACHE)))
    _DOC_CACHE[path] = (mtime, doc)
    return doc


def _any_term_files(terms: list[str], corpus_root: Path) -> list[Path]:
    """Dateien, die mindestens einen Term enthalten. Nur fuer den Teil-Treffer-Fallback."""
    needles = [term.lower() for term in terms]
    found: list[Path] = []
    for path in corpus_root.rglob("*.md"):
        text = path.read_text(encoding="utf-8", errors="replace").lower()
        if any(needle in text for needle in needles):
            found.append(path)
    return found


def _matched_terms(doc: Doc, terms: list[str]) -> int:
    text = " ".join([doc.title or "", *(turn.text for turn in doc.turns)]).lower()
    return sum(term.lower() in text for term in terms)


def _recency(doc: Doc, today: date) -> float:
    if doc.started_at is None:
        return 0.5
    age = max((today - doc.started_at.date()).days, 0)
    return float(0.5 ** (age / _RECENCY_HALFLIFE_DAYS))


def _window(text: str, start: int, end: int, before: int = 60, after: int = 90) -> str:
    """Ausschnitt um einen Treffer, an Wortgrenzen statt mitten im Wort geschnitten."""
    left = max(start - before, 0)
    if left > 0:
        space = text.rfind(" ", 0, left)
        left = space + 1 if space != -1 else 0
    right = min(end + after, len(text))
    if right < len(text):
        space = text.find(" ", right)
        right = space if space != -1 else len(text)
    return " ".join(text[left:right].split())


def _score(doc: Doc, pattern: re.Pattern[str], today: date) -> tuple[float, str]:
    total = _TITLE_WEIGHT * len(pattern.findall(doc.title or ""))
    snippet = ""
    for turn in doc.turns:
        matches = pattern.findall(turn.text)
        if not matches:
            continue
        if turn.role is not Role.USER:
            weight = _ASSISTANT_WEIGHT
        elif turn.machine_summary:
            weight = _MACHINE_SUMMARY_WEIGHT
        else:
            weight = _USER_WEIGHT
        total += weight * len(matches)
        if not snippet or (turn.role is Role.USER and not turn.machine_summary):
            hit = pattern.search(turn.text)
            if hit:
                snippet = _window(turn.text, hit.start(), hit.end())
    return total * _recency(doc, today), snippet or (doc.title or "")


def search(
    query: str,
    corpus_root: Path,
    *,
    limit: int = 10,
    include_nda: bool = False,
    since: date | None = None,
    source: str | None = None,
    project: str | None = None,
    today: date | None = None,
    ranking: Ranking = "fused",
) -> list[Hit]:
    """Beste Treffer fuer eine Query, sichtbar nach den Filtern.

    ranking="fused" (Standard) mischt zwei Ranglisten per Reciprocal Rank
    Fusion: die Substring-Suche (alle Terme, Teil-Treffer als Fallback, mit
    Recency) und BM25 ueber den ganzen Corpus. Dokumente mit allen Termen
    stehen weiterhin vorne. ranking="substring" ist das alte Verhalten und
    dient als Vergleichsbasis in den Evals.
    """
    terms = _parse_query(query)
    if not terms:
        return []
    ordered = sorted(terms, key=len, reverse=True)
    pattern = re.compile("|".join(re.escape(term) for term in ordered), re.IGNORECASE)
    now = today or datetime.now().date()

    # Was in Anfuehrungszeichen steht, muss vorkommen, auch ein einzelnes Wort.
    required = [quoted.lower() for quoted, _ in _TOKEN.findall(query) if quoted]

    def visible(path: Path) -> Doc | None:
        doc = _read_cached(path)
        if doc is None:
            return None
        if doc.sensitivity is Sensitivity.NDA and not include_nda:
            return None
        if source and doc.source.value != source:
            return None
        if project and project.lower() not in doc.project.lower():
            return None
        if since and (doc.started_at is None or doc.started_at.date() < since):
            return None
        return doc

    def collect(candidates: list[Path], partial: bool) -> list[Hit]:
        found: list[Hit] = []
        for path in candidates:
            doc = visible(path)
            if doc is None:
                continue
            score, snippet = _score(doc, pattern, now)
            if score > 0:
                matched = _matched_terms(doc, terms) if partial else len(terms)
                found.append(Hit(path, doc, score, snippet, matched, len(terms)))
        return found

    # Agents tippen oft natuerliche Wendungen. Hat kein sichtbares Dokument alle
    # Terme, liefert die Suche die besten Teil-Treffer statt einer leeren Liste
    # und markiert sie. Sichtbar heisst: nach den Filtern. Ein Volltreffer, der
    # nur in einem ausgeblendeten NDA-Dokument steht, darf den Fallback nicht
    # abschalten, sonst sieht der Agent wieder null Treffer. Im Standardmodus
    # uebernimmt BM25 die Teil-Treffer: Termzaehlen bevorzugt sonst Dokumente
    # voller Fuellwoerter.
    full_matches = collect(_candidate_files(terms, corpus_root), partial=False)
    full_matches.sort(key=lambda h: h.score, reverse=True)

    if ranking == "substring":
        hits = full_matches
        if not hits and len(terms) > 1:
            hits = collect(_any_term_files(terms, corpus_root), partial=True)
            hits.sort(key=lambda h: (h.matched_terms, h.score), reverse=True)
        return hits[:limit]

    # BM25 bewusst ohne Recency: Nutzerfragen zielen oft auf alte Sessions
    # ("weisst du noch ..."), und die Halbwertszeit hat genau die nach hinten
    # gedrueckt (echte Fragen: 5 statt 8 von 10 in den Top 5). Aktualitaet
    # bringt die Rangliste der Volltreffer in die Fusion ein.
    lexical: list[Hit] = []
    for path, bm25_score in bm25.iter_ranked(corpus_root, query):
        doc = visible(path)
        if doc is None:
            continue
        if required:
            text = " ".join([doc.title or "", *(turn.text for turn in doc.turns)]).lower()
            if not all(term in text for term in required):
                continue
        _, snippet = _score(doc, pattern, now)
        lexical.append(Hit(path, doc, bm25_score, snippet, _matched_terms(doc, terms), len(terms)))
        if len(lexical) >= _BM25_DEPTH:
            break

    # Nur Fuellwoerter oder zu kurze Terme ("to be", "C++"): BM25 findet nichts,
    # dann bleibt der alte Teil-Treffer-Fallback.
    if not full_matches and not lexical and len(terms) > 1:
        partial = collect(_any_term_files(terms, corpus_root), partial=True)
        partial.sort(key=lambda h: (h.matched_terms, h.score), reverse=True)
        return partial[:limit]

    fused: dict[Path, float] = {}
    first_seen: dict[Path, Hit] = {}
    for ranked in (full_matches, lexical):
        for rank, hit in enumerate(ranked):
            fused[hit.path] = fused.get(hit.path, 0.0) + 1.0 / (_RRF_K + rank + 1)
            first_seen.setdefault(hit.path, hit)
    merged = [
        Hit(
            path,
            first_seen[path].doc,
            score,
            first_seen[path].snippet,
            _matched_terms(first_seen[path].doc, terms),
            len(terms),
        )
        for path, score in fused.items()
    ]
    merged.sort(key=lambda h: (h.matched_terms == h.total_terms, h.score), reverse=True)
    return merged[:limit]
