"""Hybride Suche mit lokalen Embeddings, getestet mit einem kleinen Fake-Encoder."""

from __future__ import annotations

import json
import zlib
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from conftest import make_doc

from obsidiyan import dense
from obsidiyan.corpusio import write_doc
from obsidiyan.models import Role, Sensitivity, Turn
from obsidiyan.search import search

TODAY = date(2026, 7, 20)
# Der Fake-Encoder kennt eine Synonym-Bruecke, wie ein echtes Modell sie lernt.
_SYNONYMS = {"zahlungsdienstleister": "mollie", "zahlungsanbieter": "mollie"}


def fake_encoder(texts: Sequence[str], query: bool) -> np.ndarray[Any, Any]:
    vectors = np.zeros((len(texts), 64), dtype=np.float32)
    for row, text in enumerate(texts):
        for word in text.lower().replace(",", " ").replace(".", " ").split():
            word = _SYNONYMS.get(word, word)
            vectors[row, zlib.crc32(word.encode()) % 64] += 1.0
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return vectors / np.where(norms == 0, 1.0, norms)


@pytest.fixture(autouse=True)
def _fresh_cache() -> None:
    dense._MEMORY.clear()


def _doc(corpus: Path, conv_id: str, text: str, **kwargs: object) -> None:
    write_doc(make_doc(conv_id=conv_id, turns=(Turn(role=Role.USER, text=text),), **kwargs), corpus)


def test_chunks_carry_a_header_and_split_long_turns() -> None:
    head = "---\ntitle: Zahlungen\nstarted_at: '2026-01-02T10:00:00'\n---\n\n## user · x\n\n"
    text = head + "a" * 3200 + "\n"
    chunks = dense.chunk_document(text, "claude-code")
    assert len(chunks) == 3
    assert all(chunk.startswith("Zahlungen | 2026-01-02 | claude-code\n") for chunk in chunks)


def test_hybrid_bridges_a_vocabulary_gap(tmp_path: Path) -> None:
    _doc(tmp_path, "mollie", "Wir wechseln zu Mollie.")
    _doc(tmp_path, "anderes", "Server laufen bei Hetzner.")
    dense.update_index(tmp_path, fake_encoder)
    assert search("Zahlungsdienstleister", tmp_path, today=TODAY) == []
    query = "Zahlungsdienstleister"
    hits = search(query, tmp_path, today=TODAY, ranking="hybrid", encoder=fake_encoder)
    assert hits[0].doc.conv_id == "mollie"


def test_hybrid_respects_nda_filter(tmp_path: Path) -> None:
    _doc(tmp_path, "geheim", "Mandant zahlt mit Mollie", sensitivity=Sensitivity.NDA)
    dense.update_index(tmp_path, fake_encoder)
    hits = search("Zahlungsanbieter", tmp_path, today=TODAY, ranking="hybrid", encoder=fake_encoder)
    assert hits == []


def test_hybrid_without_index_equals_fused(tmp_path: Path) -> None:
    _doc(tmp_path, "a", "Brevo verschickt den Newsletter")
    fused = search("Newsletter", tmp_path, today=TODAY)
    hybrid = search("Newsletter", tmp_path, today=TODAY, ranking="hybrid", encoder=fake_encoder)
    assert [h.path for h in hybrid] == [h.path for h in fused]


def test_update_reencodes_only_changed_documents(tmp_path: Path) -> None:
    _doc(tmp_path, "a", "erste Notiz")
    _doc(tmp_path, "b", "zweite Notiz")
    assert dense.update_index(tmp_path, fake_encoder) == (2, 2)
    assert dense.update_index(tmp_path, fake_encoder) == (0, 2)
    _doc(tmp_path, "c", "dritte Notiz")
    assert dense.update_index(tmp_path, fake_encoder) == (1, 3)


def test_tampered_manifest_cannot_point_outside_the_corpus(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    _doc(corpus, "a", "Mollie")
    dense.update_index(corpus, fake_encoder)
    manifest_file = dense.index_dir(corpus) / "manifest.json"
    manifest = json.loads(manifest_file.read_text())
    manifest["docs"] = ["../../geheim.md"]
    manifest_file.write_text(json.dumps(manifest))
    dense._MEMORY.clear()
    assert list(dense.iter_ranked(corpus, "Mollie", encoder=fake_encoder)) == []


def test_hybrid_runs_when_no_keyword_matches(tmp_path: Path) -> None:
    """Mehrwortfrage ohne Stichworttreffer: genau dort muss das Embedding greifen."""
    _doc(tmp_path, "mollie", "Wir wechseln zu Mollie.")
    dense.update_index(tmp_path, fake_encoder)
    query = "welcher Zahlungsdienstleister gilt"
    hits = search(query, tmp_path, today=TODAY, ranking="hybrid", encoder=fake_encoder)
    assert [h.doc.conv_id for h in hits][:1] == ["mollie"]


def test_corrupt_index_raises_unavailable_and_embed_rebuilds(tmp_path: Path) -> None:
    _doc(tmp_path, "a", "Mollie")
    dense.update_index(tmp_path, fake_encoder)
    (dense.index_dir(tmp_path) / "manifest.json").write_text("{kaputt")
    dense._MEMORY.clear()
    with pytest.raises(dense.EmbeddingsUnavailable):
        search("Mollie", tmp_path, today=TODAY, ranking="hybrid", encoder=fake_encoder)
    assert dense.update_index(tmp_path, fake_encoder) == (1, 1)


def test_interrupted_embed_resumes_from_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for i in range(4):
        _doc(tmp_path, f"d{i}", f"Notiz Nummer {i}")
    monkeypatch.setattr(dense, "_BATCH", 1)
    monkeypatch.setattr(dense, "_CHECKPOINT_EVERY", 1)
    calls = 0

    def flaky(texts: Sequence[str], query: bool) -> np.ndarray[Any, Any]:
        nonlocal calls
        calls += 1
        if calls == 3:
            raise KeyboardInterrupt
        return fake_encoder(texts, query)

    with pytest.raises(KeyboardInterrupt):
        dense.update_index(tmp_path, flaky)
    assert dense.update_index(tmp_path, fake_encoder) == (2, 4)


def test_reads_index_written_before_versioned_vector_files(tmp_path: Path) -> None:
    """Ein Index im ersten Format (vectors.npy, ohne config) bleibt nutzbar."""
    _doc(tmp_path, "a", "Mollie")
    dense.update_index(tmp_path, fake_encoder)
    directory = dense.index_dir(tmp_path)
    manifest = json.loads((directory / "manifest.json").read_text())
    vector_file = directory / manifest.pop("vectors")
    vector_file.rename(directory / "vectors.npy")
    manifest.pop("config")
    (directory / "manifest.json").write_text(json.dumps(manifest))
    dense._MEMORY.clear()
    assert dense.update_index(tmp_path, fake_encoder) == (0, 1)
