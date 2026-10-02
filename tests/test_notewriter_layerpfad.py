"""remember_private muss eine vorhandene Note finden, egal wo im Layer sie liegt.

Am 2026-09-16 hat remember_private eine zweite
private/teamco-website/teamco-website-inhalt-und-stack.md angelegt, obwohl
private/teamco-website-inhalt-und-stack.md schon existierte. Zwei Notes gleichen
Namens im selben Layer machen jeden [[wikilink]] darauf mehrdeutig, und
verify-graph ist sofort rot gegangen.

Die Ursache sind zwei Konventionen fuer denselben Ort. emit sucht im ganzen
Layer (_find_existing) und legt eine neue Note neben ihren Parent
(_dir_beside_parent). remember_private baut seinen Pfad dagegen blind als
private/<projekt>/<titel>.md, ohne nachzusehen, ob die Note schon woanders
liegt.

Der Projektordner bleibt die Regel fuer neue Notes. Eine vorhandene Note
gewinnt aber gegen die Regel, sonst entsteht die Dublette.
"""

from __future__ import annotations

from pathlib import Path

from obsidiyan.notewriter import remember_private

BESTEHEND = """---
created: 2026-09-01
modified: 2026-09-01
parent: "[[private/INDEX]]"
---

# Mandat Thema

Von Hand gepflegt.
"""


def _layer(tmp_path: Path) -> Path:
    private = tmp_path / "private"
    private.mkdir()
    (private / "INDEX.md").write_text("# Privater Index\n", encoding="utf-8")
    return private


def test_vorhandene_note_in_der_wurzel_wird_ergaenzt(tmp_path: Path) -> None:
    """Der Fall von heute: die Note liegt flach, das Projekt hiesse ein Ordner."""
    private = _layer(tmp_path)
    ziel = private / "mandat-thema.md"
    ziel.write_text(BESTEHEND, encoding="utf-8")

    ergebnis = remember_private("Mandat", "Mandat Thema", "Ein Nachtrag.", private, section="N")

    assert ergebnis.ok, ergebnis.error
    assert ergebnis.appended and not ergebnis.created
    assert ergebnis.path == ziel
    assert not (private / "mandat" / "mandat-thema.md").exists()
    assert "Ein Nachtrag." in ziel.read_text(encoding="utf-8")


def test_vorhandene_note_in_einem_fremden_ordner_wird_ergaenzt(tmp_path: Path) -> None:
    private = _layer(tmp_path)
    (private / "acme").mkdir()
    ziel = private / "acme" / "mandat-thema.md"
    ziel.write_text(BESTEHEND, encoding="utf-8")

    ergebnis = remember_private("Mandat", "Mandat Thema", "Ein Nachtrag.", private, section="N")

    assert ergebnis.ok, ergebnis.error
    assert ergebnis.path == ziel
    assert not (private / "mandat" / "mandat-thema.md").exists()


def test_neue_note_landet_weiterhin_im_projektordner(tmp_path: Path) -> None:
    """Die Gegenprobe: ohne Vorgaengerin gilt die Projektordner-Regel."""
    private = _layer(tmp_path)

    ergebnis = remember_private(
        "Mandat", "Ganz Neu", "Inhalt.", private, parent="private/INDEX"
    )

    assert ergebnis.ok, ergebnis.error
    assert ergebnis.created
    assert ergebnis.path == private / "mandat" / "ganz-neu.md"


def test_derselbe_name_zweimal_im_layer_wird_abgelehnt(tmp_path: Path) -> None:
    """Lieber eine Absage als raten, welche der beiden gemeint ist."""
    private = _layer(tmp_path)
    for ordner in ("a", "b"):
        (private / ordner).mkdir()
        (private / ordner / "mandat-thema.md").write_text(BESTEHEND, encoding="utf-8")

    ergebnis = remember_private("Mandat", "Mandat Thema", "Ein Nachtrag.", private)

    assert not ergebnis.ok
    assert "mehrfach" in ergebnis.error.lower()


def test_memory_ordner_zaehlt_nicht_als_treffer(tmp_path: Path) -> None:
    """private/memory/ ist automatisch befuellt und wird nie kuratiert."""
    private = _layer(tmp_path)
    (private / "memory" / "projekt").mkdir(parents=True)
    (private / "memory" / "projekt" / "mandat-thema.md").write_text(BESTEHEND, encoding="utf-8")

    ergebnis = remember_private(
        "Mandat", "Mandat Thema", "Inhalt.", private, parent="private/INDEX"
    )

    assert ergebnis.ok, ergebnis.error
    assert ergebnis.path == private / "mandat" / "mandat-thema.md"
