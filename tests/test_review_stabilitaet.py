"""Eine Review muss ueberleben, wenn nur das Datum der Quelle wandert.

Der Fall ist am 2026-09-01 zweimal an einem Tag aufgetreten. Memory-Snapshots
ohne created/modified erben ihr Datum von der Dateizeit. Schreibt ein Skript
sie neu, etwa weil es eine parent-Zeile ins Frontmatter setzt, springt die
mtime, das Datum im Corpus-Pfad wandert mit, und 26 laengst destillierte
Quellen standen wieder in der Warteschlange. Kein Zeichen Body-Text hatte sich
geaendert: 46 Dateien, +67 Zeilen, -0, ausschliesslich Frontmatter.

Zwei Ursachen, hier einzeln festgenagelt:

1. `source_fingerprint` hasht den Zeitstempel der Turns mit. Bei diesen
   Quellen ist der Zeitstempel aber aus dem Dateidatum abgeleitet und damit
   Metadatum, nicht Inhalt. Der Fingerprint meldete eine Inhaltsrevision, wo
   keine war.
2. `ReviewStore.current_sources` matcht hart ueber die doc_id, die das Datum
   im Pfad traegt. Claims loesen seit resolve_doc_id() ueber die stabile
   Kurz-ID auf, Reviews nicht.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from conftest import make_doc

from obsidiyan.corpusio import write_doc
from obsidiyan.distill.select import select, source_fingerprint
from obsidiyan.distill.store import ReviewStore
from obsidiyan.models import Role, Turn


def _doc(day: str, text: str = "eine belegte Aussage ueber das Projekt"):
    start = datetime.fromisoformat(f"{day}T10:00:00+00:00")
    return make_doc(
        conv_id="stabil-1",
        day=day,
        turns=(
            Turn(role=Role.USER, ts=start, text=text),
            Turn(role=Role.ASSISTANT, ts=start, text="verstanden"),
        ),
    )


def test_fingerprint_ignoriert_das_wandernde_datum() -> None:
    """Gleicher Nutzertext, anderes Datum: derselbe Fingerprint."""
    assert source_fingerprint(_doc("2026-07-16")) == source_fingerprint(_doc("2026-09-01"))


def test_fingerprint_meldet_echte_inhaltsaenderung_weiterhin() -> None:
    """Die Gegenprobe, damit der Fix nicht einfach alles gleich macht."""
    alt = source_fingerprint(_doc("2026-07-16", "die alte Aussage"))
    neu = source_fingerprint(_doc("2026-07-16", "die geaenderte Aussage"))
    assert alt != neu


def test_review_ueberlebt_das_umdatieren_der_quelle(tmp_path: Path) -> None:
    """Der Durchstich: nach dem Umdatieren steht die Quelle nicht wieder offen."""
    corpus = tmp_path / "corpus"
    alt_pfad = write_doc(_doc("2026-07-16"), corpus)
    alt_id = alt_pfad.relative_to(corpus).as_posix()

    store = ReviewStore(tmp_path / "reviews.json")
    kandidat = select(corpus, min_user_chars=0)[0]
    from obsidiyan.distill.models import ReviewReason, SourceReview

    store.add([
        SourceReview(
            doc_id=alt_id,
            source_fingerprint=kandidat.source_fingerprint,
            reason=ReviewReason.TRANSIENT,
            reviewed_on=datetime.now(UTC).date(),
        )
    ])

    # sync-memory.sh schreibt die Datei neu, das Datum springt.
    alt_pfad.unlink()
    write_doc(_doc("2026-09-01"), corpus)

    kandidaten = select(corpus, min_user_chars=0)
    fingerprints = {c.doc_id: c.source_fingerprint for c in kandidaten}
    erledigt = store.current_sources(fingerprints)

    assert [c.doc_id for c in kandidaten if c.doc_id not in erledigt] == []
