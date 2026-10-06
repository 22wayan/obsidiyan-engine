"""BM25-Ranking ueber den Corpus, ohne zusaetzliche Abhaengigkeiten.

Die Substring-Suche zaehlt jedes Vorkommen gleich: "ich" wiegt so viel wie
"Brevo". Bei ganzen Saetzen, wie Nutzer und Agents sie tippen, landen dann
Dokumente voller Fuellwoerter vorne. BM25 gewichtet seltene Woerter hoeher
(IDF) und daempft lange Dokumente. Der Index liegt als Cache neben dem Corpus
und wird neu gebaut, sobald sich eine Corpus-Datei aendert.

Cache-Format: eine Datei mit JSON-Kopf und rohen uint32-Arrays. Kein pickle,
damit eine manipulierte Cache-Datei keinen Code ausfuehren kann.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import re
import struct
import tempfile
from array import array
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

_K1 = 1.2
_B = 0.75
_TITLE_REPEAT = 2  # Session-Titel benennen oft das Thema, das die Turns nicht wiederholen
_FORMAT = 2
INDEX_DIR = ".search-index"
_INDEX_FILE = "bm25.bin"
_MAGIC = b"OBM25"

_WORD = re.compile(r"[^\W_]+")
_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
_TITLE = re.compile(r"^title: ?(.*)$", re.MULTILINE)

# Haeufige deutsche und englische Funktionswoerter. IDF wuerde sie ohnehin
# klein rechnen; sie rauszunehmen spart Indexgroesse und haelt lange Fragen
# frei von Rauschen. Bewusst kurz: Inhaltswoerter bleiben drin.
_STOPWORD_TEXT = """
    aber alle alles als also am an auch auf aus bei bin bis bist da dann das dass
    dem den der des die dies diese dieser doch dort du durch ein eine einem einen
    einer eines er es etwas euch for fuer für hab habe haben hast hat hatte hatten
    ich ihr im in ist ja jetzt kann kannst mal man mich mir mit muss nach nicht noch
    nun nur ob oder schon sein sich sie sind so soll um und uns unter vom von vor
    war waren was weil wenn wer wie wieder wir wird wo zu zum zur
    a an and are as at be but by can do for from has have how i if in into is it
    its me my of on or so that the then there this to was we were what when which
    with you your
"""
_STOPWORDS = frozenset(_STOPWORD_TEXT.split())

# Aendert sich die Tokenisierung, passt ein alter Cache nicht mehr, obwohl der
# Corpus gleich geblieben ist. Der Fingerabdruck erzwingt dann einen Neubau.
_CONFIG = hashlib.sha256(
    json.dumps([_FORMAT, _TITLE_REPEAT, _WORD.pattern, sorted(_STOPWORDS)]).encode()
).hexdigest()[:16]


def tokenize(text: str) -> list[str]:
    return [t for t in _WORD.findall(text.lower()) if len(t) > 1 and t not in _STOPWORDS]


Signature = tuple[int, int, int]


@dataclass
class Bm25Index:
    signature: Signature
    paths: list[str]
    lengths: array[int]
    # term -> (Offset in data, Anzahl Dokumente); data haelt je Term erst die
    # Dokument-Nummern, dann die Termfrequenzen, beides uint32.
    terms: dict[str, tuple[int, int]] = field(default_factory=dict)
    data: array[int] = field(default_factory=lambda: array("I"))

    def _postings(self, term: str) -> tuple[array[int], array[int]] | None:
        entry = self.terms.get(term)
        if entry is None:
            return None
        offset, count = entry
        return self.data[offset : offset + count], self.data[offset + count : offset + 2 * count]

    def rank(self, query: str) -> list[tuple[str, float]]:
        """Alle Dokumente mit mindestens einem Query-Wort, bester Score zuerst."""
        n_docs = len(self.paths)
        avgdl = (sum(self.lengths) / n_docs) if n_docs else 1.0
        scores: dict[int, float] = {}
        for term in set(tokenize(query)):
            postings = self._postings(term)
            if postings is None:
                continue
            docs, freqs = postings
            idf = math.log(1 + (n_docs - len(docs) + 0.5) / (len(docs) + 0.5))
            for doc, tf in zip(docs, freqs, strict=True):
                norm = _K1 * (1 - _B + _B * self.lengths[doc] / avgdl)
                scores[doc] = scores.get(doc, 0.0) + idf * tf * (_K1 + 1) / (tf + norm)
        best = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        return [(self.paths[doc], score) for doc, score in best]


def _corpus_files(corpus_root: Path) -> list[Path]:
    files: list[Path] = []
    for directory, subdirs, names in os.walk(corpus_root):
        subdirs[:] = sorted(d for d in subdirs if not d.startswith("."))
        files.extend(Path(directory) / name for name in sorted(names) if name.endswith(".md"))
    return files


def _signature(files: list[Path]) -> Signature:
    count = size = mtime = 0
    for path in files:
        try:
            stat = path.stat()
        except FileNotFoundError:  # waehrend eines Ingests ersetzt
            continue
        count += 1
        size += stat.st_size
        mtime += stat.st_mtime_ns
    return count, size, mtime


def _doc_tokens(text: str) -> list[str]:
    match = _FRONTMATTER.match(text)
    if not match:
        return tokenize(text)
    title = _TITLE.search(match.group(1))
    title_tokens = tokenize(title.group(1).strip("'\"")) if title else []
    return title_tokens * _TITLE_REPEAT + tokenize(text[match.end() :])


def build(corpus_root: Path, files: list[Path] | None = None) -> Bm25Index:
    files = _corpus_files(corpus_root) if files is None else files
    paths: list[str] = []
    lengths = array("I")
    lists: dict[str, tuple[list[int], list[int]]] = {}
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            continue
        doc = len(paths)
        paths.append(path.relative_to(corpus_root).as_posix())
        tokens = _doc_tokens(text)
        lengths.append(len(tokens))
        for term, tf in Counter(tokens).items():
            docs, freqs = lists.setdefault(term, ([], []))
            docs.append(doc)
            freqs.append(tf)

    index = Bm25Index(signature=_signature(files), paths=paths, lengths=lengths)
    for term, (docs, freqs) in lists.items():
        index.terms[term] = (len(index.data), len(docs))
        index.data.extend(docs)
        index.data.extend(freqs)
    return index


def _write(index: Bm25Index, cache_file: Path) -> None:
    header = json.dumps(
        {
            "config": _CONFIG,
            "signature": list(index.signature),
            "paths": index.paths,
            "lengths": index.lengths.tolist(),
            "terms": index.terms,
        }
    ).encode("utf-8")
    cache_file.parent.mkdir(exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=cache_file.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(_MAGIC + struct.pack("<Q", len(header)) + header)
            index.data.tofile(handle)
        os.replace(tmp_name, cache_file)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def _read(cache_file: Path, signature: Signature) -> Bm25Index | None:
    raw = cache_file.read_bytes()
    if not raw.startswith(_MAGIC):
        return None
    start = len(_MAGIC) + 8
    (header_len,) = struct.unpack("<Q", raw[len(_MAGIC) : start])
    header = json.loads(raw[start : start + header_len].decode("utf-8"))
    if header.get("config") != _CONFIG or tuple(header.get("signature", ())) != signature:
        return None
    data = array("I")
    data.frombytes(raw[start + header_len :])
    terms = {term: (int(entry[0]), int(entry[1])) for term, entry in header["terms"].items()}
    if any(offset + 2 * count > len(data) for offset, count in terms.values()):
        return None
    return Bm25Index(
        signature=signature,
        paths=[str(p) for p in header["paths"]],
        lengths=array("I", header["lengths"]),
        terms=terms,
        data=data,
    )


_MEMORY: dict[Path, Bm25Index] = {}


def load(corpus_root: Path) -> Bm25Index:
    """Index aus dem Speicher, dem Cache auf Platte oder frisch gebaut.

    Der Cache gilt nur, solange Dateizahl, Gesamtgroesse und Aenderungszeiten
    des Corpus und die Tokenisierung gleich sind. Er ist abgeleitet: loeschen
    ist jederzeit gefahrlos. Kann er nicht gelesen oder geschrieben werden,
    rechnet die Suche mit dem Index im Speicher weiter.
    """
    root = corpus_root.resolve()
    if not root.is_dir():
        return Bm25Index(signature=(0, 0, 0), paths=[], lengths=array("I"))
    files = _corpus_files(root)
    signature = _signature(files)
    cached = _MEMORY.get(root)
    if cached is not None and cached.signature == signature:
        return cached

    cache_file = root / INDEX_DIR / _INDEX_FILE
    index: Bm25Index | None = None
    try:
        if cache_file.exists():
            index = _read(cache_file, signature)
    except Exception:  # ein kaputter Cache ist nur ein Cache
        index = None
    if index is None:
        index = build(root, files)
        # Schreibgeschuetzt oder voll: der Index im Speicher reicht.
        with contextlib.suppress(OSError):
            _write(index, cache_file)
    _MEMORY[root] = index
    return index


def iter_ranked(corpus_root: Path, query: str) -> Iterator[tuple[Path, float]]:
    """Ranking als absolute Pfade; Eintraege ausserhalb des Corpus werden verworfen."""
    for rel, score in load(corpus_root).rank(query):
        candidate = Path(rel)
        if candidate.is_absolute() or ".." in candidate.parts:
            continue
        yield corpus_root / candidate, score
