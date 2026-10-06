"""BM25-Ranking: Gewichtung seltener Woerter, Cache und Einbindung in die Suche."""

from __future__ import annotations

import json
import struct
from datetime import date
from pathlib import Path

import pytest
from conftest import make_doc

from obsidiyan import bm25
from obsidiyan.corpusio import write_doc
from obsidiyan.models import Role, Sensitivity, Turn
from obsidiyan.search import search

TODAY = date(2026, 7, 20)


@pytest.fixture(autouse=True)
def _fresh_memory() -> None:
    bm25._MEMORY.clear()


def _doc(corpus: Path, conv_id: str, text: str, **kwargs: object) -> None:
    write_doc(make_doc(conv_id=conv_id, turns=(Turn(role=Role.USER, text=text),), **kwargs), corpus)


def _ranked_names(corpus: Path, query: str) -> list[str]:
    return [path.name for path, _ in bm25.iter_ranked(corpus, query)]


def test_tokenize_drops_function_words_and_keeps_names() -> None:
    assert bm25.tokenize("Ich hab doch das Brevo-Modul und die FSRS-Logik") == [
        "brevo",
        "modul",
        "fsrs",
        "logik",
    ]


def test_rare_word_outweighs_frequent_filler(tmp_path: Path) -> None:
    """Eine ganze Frage darf nicht an Fuellwoertern haengen bleiben."""
    _doc(tmp_path, "brevo", "Der Newsletter laeuft ueber Brevo.")
    for i in range(5):
        _doc(tmp_path, f"filler-{i}", "wir haben das schon mal so gemacht, weisst du noch")
    question = "weisst du noch, welches Tool wir fuer den Newsletter haben?"
    hits = search(question, tmp_path, today=TODAY)
    assert hits[0].doc.conv_id == "brevo"


def test_bm25_respects_nda_filter(tmp_path: Path) -> None:
    _doc(tmp_path, "geheim", "Mandant Projektcode Falke", sensitivity=Sensitivity.NDA)
    _doc(tmp_path, "offen", "allgemeine Notiz")
    assert search("Projektcode Falke", tmp_path, today=TODAY) == []
    full = search("Projektcode Falke", tmp_path, include_nda=True, today=TODAY)
    assert [h.doc.conv_id for h in full] == ["geheim"]


def test_filters_apply_before_the_depth_cut(tmp_path: Path) -> None:
    """Ein passendes Dokument hinter vielen ausgeblendeten darf nicht wegfallen."""
    for i in range(250):
        _doc(tmp_path, f"geheim-{i}", "Hetzner Hetzner Hetzner", sensitivity=Sensitivity.NDA)
    _doc(tmp_path, "offen", "ein Server bei Hetzner und eine lange Notiz " + "fuelltext " * 50)
    hits = search("Hetzner Server Kosten", tmp_path, today=TODAY)
    assert [h.doc.conv_id for h in hits] == ["offen"]


def test_quoted_phrase_must_occur(tmp_path: Path) -> None:
    _doc(tmp_path, "phrase", "wir nehmen die rote Karte")
    _doc(tmp_path, "woerter", "Karte ist rot, die Rote Liste nicht")
    hits = search('"rote Karte"', tmp_path, today=TODAY)
    assert [h.doc.conv_id for h in hits] == ["phrase"]


def test_quoted_single_word_must_occur(tmp_path: Path) -> None:
    _doc(tmp_path, "mit", "Brevo verschickt den Newsletter")
    _doc(tmp_path, "ohne", "Newsletter Newsletter Newsletter")
    hits = search('"Brevo" Newsletter', tmp_path, today=TODAY)
    assert [h.doc.conv_id for h in hits] == ["mit"]


def test_stopword_only_query_keeps_partial_fallback(tmp_path: Path) -> None:
    _doc(tmp_path, "zitat", "to be or not to be")
    _doc(tmp_path, "anderes", "something else entirely")
    hits = search("to be or not", tmp_path, today=TODAY)
    assert [h.doc.conv_id for h in hits] == ["zitat"]


def test_missing_corpus_returns_nothing(tmp_path: Path) -> None:
    assert search("Hetzner", tmp_path / "fehlt", today=TODAY) == []


def test_unwritable_cache_does_not_break_search(tmp_path: Path) -> None:
    _doc(tmp_path, "doc", "Server bei Hetzner")
    (tmp_path / bm25.INDEX_DIR).write_text("kein Ordner")  # mkdir schlaegt fehl
    assert [h.doc.conv_id for h in search("Hetzner", tmp_path, today=TODAY)] == ["doc"]


def test_cache_is_rebuilt_when_corpus_changes(tmp_path: Path) -> None:
    _doc(tmp_path, "alt", "erste Notiz ueber Hetzner")
    assert _ranked_names(tmp_path, "Mollie") == []
    assert (tmp_path / bm25.INDEX_DIR / "bm25.bin").exists()
    _doc(tmp_path, "neu", "Zahlungen laufen jetzt ueber Mollie")
    assert len(_ranked_names(tmp_path, "Mollie")) == 1


def test_corrupt_cache_is_rebuilt(tmp_path: Path) -> None:
    _doc(tmp_path, "doc", "Hetzner Server")
    bm25.load(tmp_path)
    cache_file = tmp_path / bm25.INDEX_DIR / "bm25.bin"
    cache_file.write_bytes(b"kaputt")
    bm25._MEMORY.clear()
    assert _ranked_names(tmp_path, "Hetzner")
    assert cache_file.read_bytes().startswith(b"OBM25")


def test_tampered_cache_cannot_point_outside_the_corpus(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    _doc(corpus, "doc", "Hetzner Server")
    bm25.load(corpus)
    cache_file = corpus / bm25.INDEX_DIR / "bm25.bin"
    raw = cache_file.read_bytes()
    start = len(b"OBM25") + 8
    (length,) = struct.unpack("<Q", raw[5:start])
    header = json.loads(raw[start : start + length])
    header["paths"] = ["../../etc/passwd.md"]
    new_header = json.dumps(header).encode()
    prefix = b"OBM25" + struct.pack("<Q", len(new_header)) + new_header
    cache_file.write_bytes(prefix + raw[start + length :])
    bm25._MEMORY.clear()
    assert _ranked_names(corpus, "Hetzner") == []
