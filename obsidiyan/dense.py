"""Lokale Embeddings fuer die hybride Suche (optional: pip install -e ".[embeddings]").

Stichwortsuche scheitert an Wortluecken: "Zahlungsdienstleister" findet keine
Session, in der nur "Mollie" steht. Ein Embedding-Modell bildet Bedeutung ab
und schliesst diese Luecke. Alles laeuft lokal, kein Text verlaesst die
Maschine; das Modell wird einmal von Hugging Face geladen.

Index: ein Vektor je Turn (lange Turns geteilt), vorangestellt ein Kopf mit
Titel, Datum und Quelle. Gespeichert unter corpus/.search-index/dense-<modell>/
als JSON-Manifest plus float16-Matrix. Neu gerechnet werden nur Dokumente,
deren Inhalt sich geaendert hat. Der Index enthaelt auch vertrauliche
Dokumente; die Suche filtert sie wie ueberall erst beim Abfragen heraus.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from obsidiyan.bm25 import INDEX_DIR, _corpus_files

if TYPE_CHECKING:
    import numpy as np

DEFAULT_MODEL = "Qwen/Qwen3-Embedding-0.6B"
# Qwen3 erwartet fuer Anfragen eine Instruktion; sentence-transformers kennt sie
# als prompt_name "query". Dokumente werden ohne Prefix kodiert.
_QUERY_PROMPTS = {"Qwen/Qwen3-Embedding-0.6B": "query"}
_MAX_CHARS = 1500
_MIN_CHARS = 40
_FORMAT = 1
_TURN = re.compile(r"^## (?:user|assistant) · \S+\n\n(.*?)(?=^## |\Z)", re.MULTILINE | re.DOTALL)
_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)

# Encoder: Liste von Texten rein, normierte Vektoren raus. query=True setzt den
# Anfrage-Prompt. In Tests ersetzt ein kleiner deterministischer Encoder das Modell.
Encoder = Callable[[Sequence[str], bool], "np.ndarray[Any, Any]"]


class EmbeddingsUnavailable(RuntimeError):
    """Das optionale Extra ist nicht installiert oder es gibt noch keinen Index."""


def _field(frontmatter: str, name: str) -> str:
    match = re.search(rf"^{name}: ?(.*)$", frontmatter, re.MULTILINE)
    return match.group(1).strip("'\" ") if match else ""


def chunk_document(text: str, source: str) -> list[str]:
    """Turn-Chunks mit Kopfzeile; Dokumente ohne Turns werden als Ganzes gekuerzt."""
    match = _FRONTMATTER.match(text)
    frontmatter = match.group(1) if match else ""
    head = f"{_field(frontmatter, 'title')} | {_field(frontmatter, 'started_at')[:10]} | {source}\n"
    chunks: list[str] = []
    for turn in _TURN.finditer(text):
        body = " ".join(turn.group(1).split())
        for start in range(0, len(body), _MAX_CHARS):
            piece = body[start : start + _MAX_CHARS]
            if len(piece) > _MIN_CHARS:
                chunks.append(head + piece)
    if not chunks:
        rest = text[match.end() :] if match else text
        chunks.append(head + " ".join(rest.split())[:_MAX_CHARS])
    return chunks


def _slug(model: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", model).strip("-").lower()


def current_model() -> str:
    """Modell aus OBSIDIYAN_EMBED_MODEL, sonst der Standard. Index und Suche nutzen dasselbe."""
    return os.environ.get("OBSIDIYAN_EMBED_MODEL") or DEFAULT_MODEL


def index_dir(corpus_root: Path, model: str | None = None) -> Path:
    return corpus_root / INDEX_DIR / f"dense-{_slug(model or current_model())}"


# Aendert sich das Zerschneiden, passen alte Chunks nicht mehr, obwohl der Inhalt
# gleich ist. Der Fingerabdruck erzwingt dann einen vollen Neubau.
_CONFIG = hashlib.sha256(
    json.dumps([_FORMAT, _MAX_CHARS, _MIN_CHARS, "head-v1"]).encode()
).hexdigest()[:16]


def _numpy() -> Any:
    try:
        import numpy
    except ImportError as exc:
        raise EmbeddingsUnavailable(
            'embeddings need the optional extra: uv pip install -e ".[embeddings]"'
        ) from exc
    return numpy


def sentence_transformer_encoder(model: str | None = None, *, local_only: bool = False) -> Encoder:
    """Encoder ueber sentence-transformers.

    local_only=True (Suche) laedt nur aus dem lokalen Cache und spricht nicht mit
    Hugging Face; nur `obsidiyan embed` darf das Modell herunterladen.
    """
    model = model or current_model()
    np = _numpy()
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:  # pragma: no cover - haengt am optionalen Extra
        raise EmbeddingsUnavailable(
            'embeddings need the optional extra: uv pip install -e ".[embeddings]"'
        ) from exc
    try:
        st = SentenceTransformer(model, local_files_only=local_only)
    except Exception as exc:  # Download, MPS oder kaputter Cache: Suche faellt zurueck
        raise EmbeddingsUnavailable(f"embedding model {model} could not be loaded: {exc}") from exc
    st.max_seq_length = 512
    prompt = _QUERY_PROMPTS.get(model)

    def encode(texts: Sequence[str], query: bool) -> Any:
        return np.asarray(
            st.encode(
                list(texts),
                prompt_name=prompt if query else None,
                batch_size=32,
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
        )

    return encode


@dataclass
class DenseIndex:
    model: str
    docs: list[str]  # relativer Pfad je Chunk-Besitzer
    owners: np.ndarray[Any, Any]
    vectors: np.ndarray[Any, Any]
    hashes: dict[str, str]  # nur fertig kodierte Dokumente


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:20]


def _content_key(text: str) -> str:
    """Hash ueber Titel und Text, ohne die uebrigen Metadaten.

    Der taegliche Refresh schreibt Frontmatter neu (Datum, Parent-Links), ohne
    den Inhalt zu aendern. Ein Hash ueber die ganze Datei liess dann taeglich
    rund 1.600 Kurs-Dokumente neu kodieren.
    """
    match = _FRONTMATTER.match(text)
    if not match:
        return _hash(text)
    return _hash(_field(match.group(1), "title") + "\n" + text[match.end() :])


def _load(directory: Path, model: str) -> DenseIndex | None:
    """Index von Platte; jeder Defekt heisst "neu bauen", nie Absturz."""
    np = _numpy()
    manifest_file = directory / "manifest.json"
    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        if manifest.get("format") != _FORMAT or manifest.get("model") != model:
            return None
        if manifest.get("config", _CONFIG) != _CONFIG:
            return None
        vector_name = str(manifest.get("vectors", "vectors.npy"))
        if "/" in vector_name or vector_name.startswith("."):
            return None
        vectors = np.load(directory / vector_name, allow_pickle=False).astype(np.float32)
        docs = [str(d) for d in manifest["docs"]]
        owners = np.asarray(manifest["owners"], dtype=np.int64)
        hashes = {str(k): str(v) for k, v in manifest["hashes"].items()}
    except (OSError, ValueError, KeyError, TypeError, EOFError):
        return None
    if vectors.ndim != 2 or len(owners) != len(vectors):
        return None
    if len(owners) and (owners.min() < 0 or owners.max() >= len(docs)):
        return None
    return DenseIndex(model, docs, owners, vectors, hashes)


def _atomic_write(directory: Path, name: str, write: Callable[[Any], None]) -> None:
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            write(handle)
        os.replace(tmp, directory / name)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _save(index: DenseIndex, directory: Path) -> None:
    """Erst die Vektoren unter neuem Namen, dann das Manifest, das auf sie zeigt.

    Ein Leser sieht damit immer ein zusammengehoeriges Paar; alte Vektor-Dateien
    werden danach geloescht.
    """
    np = _numpy()
    directory.mkdir(parents=True, exist_ok=True)
    vector_name = f"vectors-{os.urandom(6).hex()}.npy"
    _atomic_write(
        directory,
        vector_name,
        lambda h: np.save(h, index.vectors.astype(np.float16), allow_pickle=False),
    )
    manifest = {
        "format": _FORMAT,
        "config": _CONFIG,
        "model": index.model,
        "vectors": vector_name,
        "docs": index.docs,
        "owners": index.owners.tolist(),
        "hashes": index.hashes,
    }
    _atomic_write(directory, "manifest.json", lambda h: h.write(json.dumps(manifest).encode()))
    for old in directory.glob("vectors*.npy"):
        if old.name != vector_name:
            old.unlink(missing_ok=True)


_BATCH = 256
_CHECKPOINT_EVERY = 20  # Flushes, also etwa alle 5.000 Chunks


def update_index(
    corpus_root: Path,
    encoder: Encoder,
    *,
    model: str | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> tuple[int, int]:
    """Index anlegen oder auffrischen. Liefert (neu kodierte Dokumente, Dokumente gesamt).

    Zwischenstaende werden regelmaessig gespeichert. Bricht der Lauf ab, setzt der
    naechste dort fort: nur Dokumente mit gespeichertem Hash gelten als fertig.
    """
    np = _numpy()
    model = model or current_model()
    directory = index_dir(corpus_root, model)
    old = _load(directory, model)
    old_rows: dict[str, list[int]] = {}
    if old is not None:
        for row, owner in enumerate(old.owners.tolist()):
            old_rows.setdefault(old.docs[owner], []).append(row)

    docs: list[str] = []
    hashes: dict[str, str] = {}
    parts: list[Any] = []
    owners: list[int] = []
    pending: list[tuple[int, str, str, list[str]]] = []
    for path in _corpus_files(corpus_root):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            continue
        rel = path.relative_to(corpus_root).as_posix()
        digest = _content_key(text)
        owner = len(docs)
        docs.append(rel)
        stored = old.hashes.get(rel) if old is not None else None
        # _hash(text) erkennt Indizes, die noch mit dem Ganzdatei-Hash gebaut wurden.
        if old is not None and stored in (digest, _hash(text)) and rel in old_rows:
            parts.append(old.vectors[old_rows[rel]])
            owners.extend([owner] * len(old_rows[rel]))
            hashes[rel] = digest
        else:
            pending.append((owner, rel, digest, chunk_document(text, rel.split("/")[0])))
    old = None  # Speicher frei, bevor neue Vektoren entstehen

    def snapshot() -> None:
        vectors = np.concatenate(parts) if parts else np.zeros((0, 1), dtype=np.float32)
        _save(
            DenseIndex(model, docs, np.asarray(owners, dtype=np.int64), vectors, dict(hashes)),
            directory,
        )

    batch: list[str] = []
    batch_owners: list[int] = []
    batch_docs: list[tuple[str, str]] = []
    flushes = 0

    def flush() -> None:
        nonlocal flushes
        if not batch:
            return
        parts.append(np.asarray(encoder(batch, False), dtype=np.float32))
        owners.extend(batch_owners)
        hashes.update(batch_docs)
        batch.clear()
        batch_owners.clear()
        batch_docs.clear()
        flushes += 1
        if flushes % _CHECKPOINT_EVERY == 0:
            snapshot()

    for done, (owner, rel, digest, chunks) in enumerate(pending, start=1):
        batch.extend(chunks)
        batch_owners.extend([owner] * len(chunks))
        batch_docs.append((rel, digest))
        if len(batch) >= _BATCH:
            flush()
            if progress:
                progress(done, len(pending))
    flush()
    if progress:
        progress(len(pending), len(pending))
    snapshot()
    _MEMORY.pop((corpus_root.resolve(), model), None)
    return len(pending), len(docs)


_MEMORY: dict[tuple[Path, str], tuple[float, DenseIndex]] = {}
_ENCODERS: dict[str, Encoder] = {}


def available(corpus_root: Path, model: str | None = None) -> bool:
    return (index_dir(corpus_root, model) / "manifest.json").exists()


def _cached_index(corpus_root: Path, model: str) -> DenseIndex:
    directory = index_dir(corpus_root, model)
    manifest = directory / "manifest.json"
    try:
        stamp = manifest.stat().st_mtime
    except OSError as exc:
        raise EmbeddingsUnavailable("no embedding index, run `obsidiyan embed` first") from exc
    key = (corpus_root.resolve(), model)
    cached = _MEMORY.get(key)
    if cached is not None and cached[0] == stamp:
        return cached[1]
    _MEMORY.pop(key, None)  # alten Index freigeben, bevor der neue geladen wird
    index = _load(directory, model)
    if index is None:
        raise EmbeddingsUnavailable(
            "embedding index is unreadable or built for another setup, run `obsidiyan embed`"
        )
    _MEMORY[key] = (stamp, index)
    return index


def encoder_for(model: str | None = None) -> Encoder:
    """Prozessweit geteilter Encoder; laedt nur aus dem lokalen Cache."""
    model = model or current_model()
    if model not in _ENCODERS:
        _ENCODERS[model] = sentence_transformer_encoder(model, local_only=True)
    return _ENCODERS[model]


def iter_ranked(
    corpus_root: Path,
    query: str,
    *,
    model: str | None = None,
    encoder: Encoder | None = None,
) -> Iterator[tuple[Path, float]]:
    """Dokumente nach bester Chunk-Aehnlichkeit, absteigend; Pfade bleiben im Corpus."""
    np = _numpy()
    model = model or current_model()
    index = _cached_index(corpus_root, model)
    if len(index.vectors) == 0:
        return
    encoder = encoder or encoder_for(model)
    try:
        query_vector = np.asarray(encoder([query], True), dtype=np.float32)[0]
    except Exception as exc:
        raise EmbeddingsUnavailable(f"query could not be encoded: {exc}") from exc
    if query_vector.shape[0] != index.vectors.shape[1]:
        raise EmbeddingsUnavailable(
            "embedding index does not match the model, run `obsidiyan embed`"
        )
    scores = index.vectors @ query_vector
    seen: set[int] = set()
    for row in np.argsort(-scores):
        owner = int(index.owners[row])
        if owner in seen:
            continue
        seen.add(owner)
        candidate = Path(index.docs[owner])
        if candidate.is_absolute() or ".." in candidate.parts:
            continue
        yield corpus_root / candidate, float(scores[row])
