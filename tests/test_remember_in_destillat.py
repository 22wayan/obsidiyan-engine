"""In eine destillierte Note schreiben heisst, den Text zu verlieren.

Am 2026-09-16 hat remember_private einen Abschnitt an
private/teamco-website-inhalt-und-stack.md angehaengt und "appended": true
gemeldet. Der naechste emit hat ihn geloescht.

Der Grund ist kein Zufall und auch kein Versehen in emit: eine Note mit
distilled_from im Frontmatter wird aus dem Claim-Store neu geschrieben, das
ist ihr Zweck. Die Schutzlogik _split_at_distill_marker greift ausdruecklich
nur bei handgeschriebenen Notes.

Falsch ist deshalb nicht emit, sondern dass write_note den Verlust nicht
ansagt. Ein stiller Erfolg, dessen Ergebnis beim naechsten Lauf verschwindet,
ist schlimmer als eine Absage mit Begruendung. Dieselbe Klasse Fehler hat
schon einmal zwei Abschnitte in notes/teamco.md gekostet.
"""

from __future__ import annotations

from pathlib import Path

from obsidiyan.notewriter import remember as write_note

DESTILLAT = """---
created: 2026-09-01
modified: 2026-09-01
ai_generated: true
parent: "[[notes/brain]]"
distilled_from: 3 Quellen, 2026-08-01 bis 2026-09-01
---

# Ein Thema

## Entscheidungen

- **2026-09-01**: Eine belegte Aussage. [Q1]
"""

HANDGESCHRIEBEN = """---
created: 2026-09-01
modified: 2026-09-01
parent: "[[notes/brain]]"
---

# Eine Nabe

Von Hand gepflegt.
"""


def _layer(tmp_path: Path) -> Path:
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "brain.md").write_text("# BRAIN\n", encoding="utf-8")
    return notes


def test_anhaengen_an_ein_destillat_wird_abgelehnt(tmp_path: Path) -> None:
    notes = _layer(tmp_path)
    ziel = notes / "ein-thema.md"
    ziel.write_text(DESTILLAT, encoding="utf-8")

    ergebnis = write_note("Ein Thema", "Ein Nachtrag.", notes, section="Nachtrag")

    assert not ergebnis.ok
    assert "destilliert" in ergebnis.error.lower()
    assert ziel.read_text(encoding="utf-8") == DESTILLAT


def test_die_absage_nennt_den_weg_der_stattdessen_traegt(tmp_path: Path) -> None:
    """Eine Absage ohne Ausweg schickt den Agenten nur in die naechste Sackgasse."""
    notes = _layer(tmp_path)
    (notes / "ein-thema.md").write_text(DESTILLAT, encoding="utf-8")

    fehler = write_note("Ein Thema", "Ein Nachtrag.", notes, section="N").error

    assert "claim" in fehler.lower()


def test_handgeschriebene_notes_nehmen_weiter_an(tmp_path: Path) -> None:
    """Die Gegenprobe: der normale Weg darf nicht kaputtgehen."""
    notes = _layer(tmp_path)
    ziel = notes / "eine-nabe.md"
    ziel.write_text(HANDGESCHRIEBEN, encoding="utf-8")

    ergebnis = write_note("Eine Nabe", "Ein Nachtrag.", notes, section="Nachtrag")

    assert ergebnis.ok and ergebnis.appended
    assert "Ein Nachtrag." in ziel.read_text(encoding="utf-8")


def test_eine_neue_note_entsteht_weiterhin(tmp_path: Path) -> None:
    notes = _layer(tmp_path)

    ergebnis = write_note("Ganz neu", "Inhalt.", notes, parent="notes/brain")

    assert ergebnis.ok and ergebnis.created
