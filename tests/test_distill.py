from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from conftest import make_doc

from obsidiyan.corpusio import write_doc
from obsidiyan.distill.models import Claim, ClaimKind, Confidence
from obsidiyan.distill.select import select, user_text
from obsidiyan.distill.synthesize import render_note, resolve
from obsidiyan.models import Role, Sensitivity, Source, Turn

TODAY = date(2026, 8, 8)


def _claim(
    key: str,
    text: str,
    day: str,
    *,
    kind: ClaimKind = ClaimKind.DECISION,
    src: str = "a.md",
    rationale: str = "",
    confidence: Confidence = Confidence.HIGH,
) -> Claim:
    return Claim(
        entity="Projekt",
        topic="ini.tech",
        key=key,
        kind=kind,
        text=text,
        stated_on=date.fromisoformat(day),
        source_doc_id=src,
        rationale=rationale,
        confidence=confidence,
    )


def _emit_parent(root: Path) -> str:
    (root / "portfolio.md").write_text("# Portfolio\n", encoding="utf-8")
    return "portfolio"


def test_newest_claim_wins_and_older_is_kept_as_superseded() -> None:
    alt = _claim("preismodell", "Discovery kostenlos anbieten", "2025-11-02")
    neu = _claim("preismodell", "Discovery zum Festpreis", "2026-08-03")
    current, superseded = resolve([neu, alt])
    assert [c.text for c in current] == ["Discovery zum Festpreis"]
    assert len(superseded) == 1
    assert superseded[0][0].text == "Discovery kostenlos anbieten"
    assert superseded[0][1].text == "Discovery zum Festpreis"


def test_identical_repetition_is_not_a_contradiction() -> None:
    a = _claim("preismodell", "Discovery zum Festpreis", "2026-01-01")
    b = _claim("preismodell", "Discovery zum Festpreis", "2026-08-03")
    current, superseded = resolve([a, b])
    assert len(current) == 1
    assert superseded == []


def test_different_keys_do_not_collide() -> None:
    a = _claim("preismodell", "Festpreis", "2026-01-01")
    b = _claim("gespraechslead", "Alex fuehrt das Gespraech", "2026-01-02")
    current, superseded = resolve([a, b])
    assert len(current) == 2
    assert superseded == []


def test_every_bullet_carries_its_source() -> None:
    """Die Kerneigenschaft: keine Behauptung ohne Herkunft."""
    note = render_note(
        "ini.tech", [_claim("k", "Etwas entschieden", "2026-08-03", src="x/y.md")], TODAY
    )
    assert "[Q1]" in note
    assert "- **Q1** `corpus/x/y.md`" in note
    for line in note.splitlines():
        if line.startswith("- **2"):
            assert "[Q" in line, f"Punkt ohne Quelle: {line}"


def test_superseded_claim_cites_old_and_replacement_sources() -> None:
    old = _claim("preis", "Alter Preis", "2026-01-01", src="alt.md")
    new = _claim("preis", "Neuer Preis", "2026-08-01", src="neu.md")
    note = render_note("Thema", [old, new], TODAY)
    assert "Alter Preis [Q1]" in note
    assert "ersetzt am 2026-08-01 durch: Neuer Preis [Q2]" in note


def test_note_has_frontmatter_and_provenance_header() -> None:
    note = render_note("Thema", [_claim("k", "A", "2026-08-03")], TODAY)
    assert note.startswith("---\ncreated: 2026-08-08\n")
    assert "ai_generated: true" in note
    assert "distilled_from: 1 Quelle" in note
    assert "Ungepruefte Maschinenarbeit" in note


def test_rationale_is_rendered() -> None:
    claim = _claim(
        "k",
        "Festpreis statt kostenlos",
        "2026-08-03",
        rationale="kostenlos Wertlosigkeit indiziert",
    )
    note = render_note("Thema", [claim], TODAY)
    assert "weil kostenlos Wertlosigkeit indiziert" in note


def test_links_become_wikilinks() -> None:
    note = render_note("Thema", [_claim("k", "A", "2026-08-03")], TODAY, links=["teamco", "owner"])
    assert "[[teamco]]" in note and "[[owner]]" in note


def test_select_skips_thin_docs_but_keeps_nda(tmp_path: Path) -> None:
    """NDA ist eigenes Wissen und wird destilliert, das Ziel ist private/."""
    write_doc(make_doc(conv_id="duenn", turns=(Turn(role=Role.USER, text="kurz"),)), tmp_path)
    write_doc(
        make_doc(
            conv_id="dick",
            day="2026-08-01",
            turns=(Turn(role=Role.USER, text="x" * 3000),),
        ),
        tmp_path,
    )
    write_doc(
        make_doc(
            conv_id="geheim",
            day="2026-08-01",
            sensitivity=Sensitivity.NDA,
            turns=(Turn(role=Role.USER, text="y" * 3000),),
        ),
        tmp_path,
    )
    picked = select(tmp_path, today=TODAY)
    assert {c.doc_id.split("--")[-1] for c in picked} == {
        c.doc_id.split("--")[-1] for c in picked if c.user_chars == 3000
    }
    assert len(picked) == 2
    assert {c.sensitivity for c in picked} == {Sensitivity.CLEAN, Sensitivity.NDA}


def test_select_skips_copyright_docs(tmp_path: Path) -> None:
    """Fremdes Kursmaterial ist nicht des Nutzers Wissen, auch nicht als Destillat."""
    write_doc(
        make_doc(
            conv_id="kurs",
            day="2026-08-01",
            sensitivity=Sensitivity.COPYRIGHT,
            turns=(Turn(role=Role.USER, text="z" * 3000),),
        ),
        tmp_path,
    )

    assert select(tmp_path, today=TODAY) == []


def test_select_skips_curated_notes_to_avoid_circular_provenance(tmp_path: Path) -> None:
    curated = make_doc(
        source=Source.MEMORY,
        conv_id="notes/profil.md",
        project="notes",
        turns=(Turn(role=Role.USER, text="Kuratierte Aussage. " * 200),),
    )
    primary = make_doc(
        source=Source.MEMORY,
        conv_id="project-memory.md",
        project="produkt",
        turns=(Turn(role=Role.USER, text="Projektentscheidung. " * 200),),
    )
    write_doc(curated, tmp_path)
    write_doc(primary, tmp_path)

    picked = select(tmp_path, today=TODAY)
    assert [candidate.project for candidate in picked] == ["produkt"]


def test_select_skips_unattended_scheduled_task_prompts(tmp_path: Path) -> None:
    openings = (
        "<scheduled-task>",
        '<scheduled-task name="monitor-demo">',
        '<scheduled-task\tname="monitor-demo">',
        '<scheduled-task\nname="monitor-demo">',
        "<automated-task>",
        '<automated-task name="monitor-demo">',
    )
    for index, opening in enumerate(openings):
        automated = make_doc(
            source=Source.CODEX,
            conv_id=f"automated-{index}",
            project="monitor-demo",
            turns=(
                Turn(
                    role=Role.USER,
                    text=opening + "\n" + ("This is an automated run. " * 200),
                ),
            ),
        )
        write_doc(automated, tmp_path)

    human = make_doc(
        source=Source.CODEX,
        conv_id="human",
        project="monitor-demo",
        turns=(Turn(role=Role.USER, text="Ich will den Monitor verbessern. " * 150),),
    )
    human_path = write_doc(human, tmp_path)

    picked = select(tmp_path, today=TODAY)
    assert [candidate.doc_id for candidate in picked] == [
        human_path.relative_to(tmp_path).as_posix()
    ]


def test_select_prefers_recent_over_merely_long(tmp_path: Path) -> None:
    alt = make_doc(conv_id="alt", day="2023-01-01", turns=(Turn(role=Role.USER, text="a" * 9000),))
    neu = make_doc(conv_id="neu", day="2026-08-01", turns=(Turn(role=Role.USER, text="b" * 4000),))
    write_doc(alt, tmp_path)
    write_doc(neu, tmp_path)
    picked = select(tmp_path, today=TODAY)
    assert picked[0].user_chars == 4000


def test_user_text_excludes_assistant_turns(tmp_path: Path) -> None:
    doc = make_doc(
        conv_id="mixed",
        turns=(
            Turn(role=Role.USER, text="meine frage"),
            Turn(role=Role.ASSISTANT, text="ASSISTENT ANTWORT"),
        ),
    )
    path = write_doc(doc, tmp_path)
    text = user_text(path)
    assert "meine frage" in text
    assert "ASSISTENT ANTWORT" not in text


def test_prose_ratio_rejects_pasted_logs() -> None:
    from obsidiyan.distill.prose import prose_ratio

    log = "\n".join(
        [
            "Collecting manim",
            "  Downloading manim-0.19.1-py3-none-any.whl.metadata (11 kB)",
            '{"total":2,"data":[{"typeId":"8a24"}]}',
            "(.venv) user@mac:~/Projekt$ pip install manim",
            'File "/usr/lib/python3.12/subprocess.py", line 415, in check_call',
        ]
    )
    assert prose_ratio(log) < 0.2


def test_prose_ratio_accepts_real_writing() -> None:
    from obsidiyan.distill.prose import prose_ratio

    text = (
        "Teamco ist jetzt final, um den Verein beim Notar einzutragen brauchen wir eine Satzung.\n"
        "Deine Aufgabe ist es, die Satzungen anderer studentischer Beratungen zu vergleichen.\n"
        "Nur ich, Sam und Alex sind im Vorstand, Alex ist Schatzmeister."
    )
    assert prose_ratio(text) > 0.9


def test_medium_confidence_is_visibly_marked() -> None:
    """Fremdes Referenzmaterial darf nicht wie eine eigene Entscheidung aussehen."""
    weich = Claim(
        topic="T", key="k", kind=ClaimKind.FACT, text="Referenzprozess von Dritten",
        stated_on=date(2026, 6, 29), source_doc_id="a.md", confidence=Confidence.MEDIUM,
    )
    hart = _claim("k2", "Eigene Entscheidung", "2026-06-29", confidence=Confidence.HIGH)
    note = render_note("T", [weich, hart], TODAY)
    assert "Referenzprozess von Dritten *(indirekt belegt)*" in note
    assert "Eigene Entscheidung [Q" in note
    assert "*(indirekt belegt)*" not in note.split("Eigene Entscheidung")[1].split("\n")[0]


def test_pasted_source_code_is_not_prose() -> None:
    """Ein Pine-Script-Dump erreichte 97 Prozent Prosa, weil Kommentarzeilen wie Text aussehen."""
    from obsidiyan.distill.prose import prose_ratio

    code = "\n".join(
        [
            "//@version=6",
            "// CHANGES: Optimized object usage by reducing box count and improved handling",
            'indicator("NoPerm AI Session FVG + Sweeps + Levels", overlay=true)',
            'string lonSess = input.session("0800-1700", "London Session")',
            'bool showSweeps = input.bool(true, "Show Liquidity Sweeps")',
            "int atrLen = input.int(14, \"ATR Length\")",
            "if barstate.islast",
            "    label.new(bar_index, high, text=txt)",
            "color c = input.color(color.green, 'Breakout')",
            "float atrMult = input.float(1.2, 'ATR Multiplier')",
        ]
    )
    assert prose_ratio(code) < 0.2


def test_pasted_shell_and_html_transcript_is_not_prose() -> None:
    """Shopware-Logs mit Shell-Prompts und Cloudflare-HTML waren zu 90 % Prosa."""
    from obsidiyan.distill.prose import prose_ratio

    transcript = """Ich habe ein Problem mit den Bildern. Was soll ich jetzt tun?
Windows PowerShell
Copyright (C) Microsoft Corporation.
PS C:\\Users\\person> ssh user@example-host
user@example-host:/srv/shop$ ls
bin composer.json config custom public src var vendor
user@example-host:/srv/shop$ bin/console media:generate-thumbnails
Generating Thumbnails for 4827 files. This may take some time...
4827/4827 [============================] 100%
----------- --------------------------
Action      Number of Media Entities
Generated   0
Skipped     4827
Errors      0
----------- --------------------------
<!DOCTYPE html>
<html lang="en">
<head><title>Attention Required</title></head>
<body>
<div id="error-details">
<h1>Sorry, you have been blocked</h1>
<p>This website is using a security service.</p>
</div>
</body>
</html>
"""
    assert prose_ratio(transcript) < 0.45


def test_machine_line_recognizes_terminal_and_html_markers() -> None:
    from obsidiyan.distill.prose import is_machine_line

    lines = (
        "PS C:\\Users\\person> ssh user@example-host",
        "PS C:\\Users\\person>",
        "user@example-host:/srv/shop$ ls",
        "user@example-host:~/shop% ls",
        "user@MacBook-Pro Obsidiyan % ls",
        "user@MacBook-Pro ~ % git status",
        "% ls",
        "<!DOCTYPE html>",
        '<div id="error-details">',
        "4827/4827 [============================] 100%",
        "----------- --------------------------",
        "Windows PowerShell",
        "Copyright (C) Microsoft Corporation.",
        "Copyright (C) Microsoft Corporation. Alle Rechte vorbehalten.",
        "Last login: Thu Oct 30 12:42:01 2025",
        "Generating Thumbnails for 4827 files. This may take some time...",
        "Generated   0",
        "Skipped     4827",
        "Errors      0",
    )
    for line in lines:
        assert is_machine_line(line), line


def test_machine_line_preserves_similar_human_prose() -> None:
    from obsidiyan.distill.prose import is_machine_line

    lines = (
        "Windows PowerShell ist mein bevorzugtes Terminal.",
        "Generated 3 useful ideas for the proposal.",
        "Last login security is a hard requirement.",
        "alice@example.com: Budget $ 100 pro Monat",
    )
    for line in lines:
        assert not is_machine_line(line), line


def test_question_with_small_snippet_stays_prose() -> None:
    from obsidiyan.distill.prose import prose_ratio

    text = (
        "Ich will meinen Indikator so umbauen, dass er auch im 5-Minuten-Chart sauber laeuft.\n"
        "Aktuell steht da atrLen = 14, aber das reagiert mir zu traege.\n"
        "Was waere ein besserer Wert und warum?"
    )
    assert prose_ratio(text) > 0.6


def test_candidate_render_includes_doc_id_for_show_command() -> None:
    from obsidiyan.distill.select import Candidate

    candidate = Candidate(
        doc_id="chatgpt/2026/example.md",
        path=Path("corpus/chatgpt/2026/example.md"),
        project="Projekt",
        source="chatgpt",
        started_at=TODAY,
        user_chars=3000,
        prose=0.9,
        score=2700.0,
        source_fingerprint="0123456789abcdef",
    )
    assert "chatgpt/2026/example.md" in candidate.render()


def test_pending_excludes_already_extracted(tmp_path: Path) -> None:
    """Claim- und Review-Quellen koennen gemeinsam als erledigt uebergeben werden."""
    from obsidiyan.distill.select import pending

    for name in ("a", "b"):
        doc = make_doc(
            conv_id=name, day="2026-08-01", turns=(Turn(role=Role.USER, text="x" * 3000),)
        )
        write_doc(doc, tmp_path)
    alle = select(tmp_path, today=TODAY)
    assert len(alle) == 2
    offen = pending(tmp_path, {alle[0].doc_id}, today=TODAY)
    assert [c.doc_id for c in offen] == [alle[1].doc_id]


def test_emit_rewrites_its_own_notes_instead_of_appending(tmp_path: Path) -> None:
    """Sonst dupliziert jeder Lauf seinen eigenen Inhalt in dieselbe Datei."""
    from obsidiyan.distill.emit import emit

    claims = [_claim("k", "Erste Fassung", "2026-08-01")]
    emit(
        claims,
        tmp_path,
        today=TODAY,
        entity_parents={"Projekt": _emit_parent(tmp_path)},
    )
    emit(claims, tmp_path, today=TODAY)
    text = (tmp_path / "ini-tech.md").read_text()
    assert text.count("Erste Fassung") == 1
    assert "## Destillat" not in text


def test_emit_preserves_created_date_for_generated_note_and_hub(tmp_path: Path) -> None:
    from obsidiyan.distill.emit import emit

    claim = Claim(
        entity="Projekt",
        topic="Projekt Status",
        key="status",
        kind=ClaimKind.FACT,
        text="Aktiv",
        stated_on=date(2026, 8, 1),
        source_doc_id="a.md",
    )
    emit(
        [claim],
        tmp_path,
        today=TODAY,
        entity_parents={"Projekt": _emit_parent(tmp_path)},
    )
    emit([claim], tmp_path, today=date(2026, 8, 9))

    for path in (tmp_path / "projekt-status.md", tmp_path / "projekt.md"):
        text = path.read_text()
        assert "created: 2026-08-08" in text
        assert "modified: 2026-08-09" in text
        assert "parent:" in text


def test_frontmatter_value_requires_closing_delimiter() -> None:
    from obsidiyan.distill.emit import _frontmatter_value

    assert _frontmatter_value("---\ncreated: 2026-01-01\n# kaputt", "created") is None
    assert (
        _frontmatter_value("---\ncreated: 2026-01-01\n---\ncreated: body", "created")
        == "2026-01-01"
    )
    assert (
        _frontmatter_value(
            "---\nparent: '[[a]]'\nparent: '[[b]]'\n---\n", "parent"
        )
        is None
    )


def test_emit_appends_to_handwritten_notes(tmp_path: Path) -> None:
    """Handgeschriebenes wird nie ueberschrieben, nur datiert ergaenzt."""
    from obsidiyan.corpusio import slugify
    from obsidiyan.distill.emit import emit

    hand = tmp_path / f"{slugify('ini.tech')}.md"
    hand.write_text("---\ncreated: 2026-01-01\n---\n\n# ini.tech\n\nHandarbeit.\n")
    claims = [_claim("k", "Maschinell ergaenzt", "2026-08-01")]
    emit(
        claims,
        tmp_path,
        today=TODAY,
        entity_parents={"Projekt": _emit_parent(tmp_path)},
    )
    text = hand.read_text()
    assert "Handarbeit." in text
    assert "## Destillat 2026-08-08" in text

    emit(claims, tmp_path, today=date(2026, 8, 9))
    text = hand.read_text()
    assert text.count("Maschinell ergaenzt") == 1
    assert text.count("obsidiyan:distill:start") == 1
    assert "## Destillat 2026-08-09" in text


def test_hub_note_links_all_topics_of_an_entity(tmp_path: Path) -> None:
    """Ohne Nabe liegen die Themen als Inseln nebeneinander."""
    from obsidiyan.distill.emit import emit

    def ec(topic: str, key: str, day: str) -> Claim:
        return Claim(
            entity="nebenprojekt", topic=topic, key=key, kind=ClaimKind.DECISION,
            text=f"Aussage {key}", stated_on=date.fromisoformat(day),
            source_doc_id="a.md", confidence=Confidence.HIGH,
        )

    emit(
        [
            ec("nebenprojekt Preise", "p", "2026-07-23"),
            ec("nebenprojekt Vertrieb", "v", "2026-07-26"),
        ],
        tmp_path,
        today=TODAY,
        entity_parents={"nebenprojekt": _emit_parent(tmp_path)},
    )

    hub = (tmp_path / "nebenprojekt.md").read_text()
    assert "[[nebenprojekt-preise|nebenprojekt Preise]]" in hub
    assert "[[nebenprojekt-vertrieb|nebenprojekt Vertrieb]]" in hub
    assert "parent:" in hub
    # und zurueck: jedes Unterthema verweist auf die Nabe
    assert "[[nebenprojekt]]" in (tmp_path / "nebenprojekt-preise.md").read_text()


def test_hub_counts_only_current_open_claims() -> None:
    from obsidiyan.distill.emit import render_hub

    old = _claim("status", "Noch offen", "2026-01-01", kind=ClaimKind.OPEN)
    new = _claim("status", "Erledigt", "2026-08-01", kind=ClaimKind.FACT)
    hub = render_hub("Projekt", {"Thema": [old, new]}, TODAY)
    assert "2 Claims" in hub
    assert "offen" not in next(line for line in hub.splitlines() if "[[thema|" in line)


def test_bump_modified_never_replaces_body_content() -> None:
    from obsidiyan.distill.emit import _bump_modified

    note = "---\ncreated: 2026-01-01\n---\n\n# Note\n\nmodified: means changed\n"
    updated = _bump_modified(note, TODAY)
    assert "created: 2026-01-01\nmodified: 2026-08-08\n---" in updated
    assert "modified: means changed" in updated
    assert _bump_modified(updated, TODAY) == updated


def test_bump_modified_replaces_only_existing_frontmatter_key() -> None:
    from obsidiyan.distill.emit import _bump_modified

    note = "---\nmodified: 2026-01-01\n---\n\nmodified: prose\n"
    updated = _bump_modified(note, TODAY)
    assert updated == "---\nmodified: 2026-08-08\n---\n\nmodified: prose\n"


def test_bump_modified_leaves_text_without_frontmatter_unchanged() -> None:
    from obsidiyan.distill.emit import _bump_modified

    note = "# Note\n\nmodified: prose\n"
    assert _bump_modified(note, TODAY) == note


def test_hub_does_not_overwrite_a_handwritten_note(tmp_path: Path) -> None:
    from obsidiyan.distill.emit import emit

    hand = tmp_path / "teamco.md"
    hand.write_text("---\ncreated: 2026-01-01\n---\n\n# Teamco\n\nVon Hand geschrieben.\n")
    emit(
        [Claim(entity="Teamco", topic="Teamco Satzung", key="k", kind=ClaimKind.FACT, text="x",
               stated_on=date(2026, 6, 29), source_doc_id="a.md")],
        tmp_path,
        today=TODAY,
        entity_parents={"Teamco": _emit_parent(tmp_path)},
    )
    text = hand.read_text()
    assert "Von Hand geschrieben." in text
    assert "## Destillierte Unterthemen" in text
    assert "[[teamco-satzung|Teamco Satzung]]" in text
    assert "modified: 2026-08-08" in text
    assert "parent:" in text


def test_emit_refuses_parentless_or_entityless_new_nodes(tmp_path: Path) -> None:
    from obsidiyan.distill.emit import emit

    entityless = Claim(
        topic="Thema",
        key="k",
        kind=ClaimKind.FACT,
        text="x",
        stated_on=TODAY,
        source_doc_id="a.md",
    )
    with pytest.raises(ValueError, match="entity ist Pflicht"):
        emit([entityless], tmp_path, today=TODAY)

    parentless = Claim(
        entity="Neue Entity",
        topic="Neues Thema",
        key="k",
        kind=ClaimKind.FACT,
        text="x",
        stated_on=TODAY,
        source_doc_id="a.md",
    )
    with pytest.raises(ValueError, match="bestehenden parent"):
        emit([parentless], tmp_path, today=TODAY)
    assert not (tmp_path / "neue-entity.md").exists()


def test_emit_rejects_slug_collisions_before_writing(tmp_path: Path) -> None:
    from obsidiyan.distill.emit import emit

    claims = [
        Claim(
            entity="Projekt",
            topic="Foo Bar",
            key="a",
            kind=ClaimKind.FACT,
            text="a",
            stated_on=TODAY,
            source_doc_id="a.md",
        ),
        Claim(
            entity="Projekt",
            topic="foo-bar",
            key="b",
            kind=ClaimKind.FACT,
            text="b",
            stated_on=TODAY,
            source_doc_id="b.md",
        ),
    ]

    with pytest.raises(ValueError, match="Topic-Slug-Kollision"):
        emit(claims, tmp_path, today=TODAY)
    assert not list(tmp_path.glob("*.md"))


def test_emit_rejects_topic_that_collides_with_its_hub(tmp_path: Path) -> None:
    from obsidiyan.distill.emit import emit

    claim = Claim(
        entity="Projekt",
        topic="Projekt",
        key="k",
        kind=ClaimKind.FACT,
        text="x",
        stated_on=TODAY,
        source_doc_id="a.md",
    )

    with pytest.raises(ValueError, match="Hub-/Topic-Slug-Kollision"):
        emit([claim], tmp_path, today=TODAY)
    assert not list(tmp_path.glob("*.md"))


def test_emit_refuses_symlink_target_before_any_note_write(tmp_path: Path) -> None:
    from obsidiyan.distill.emit import emit

    outside = tmp_path.parent / f"{tmp_path.name}-outside.md"
    outside.write_text("unveraendert", encoding="utf-8")
    (tmp_path / "ini-tech.md").symlink_to(outside)

    with pytest.raises(ValueError, match="unsicheres Emit-Ziel"):
        emit(
            [_claim("k", "Neu", "2026-08-01")],
            tmp_path,
            today=TODAY,
            entity_parents={"Projekt": _emit_parent(tmp_path)},
        )

    assert outside.read_text(encoding="utf-8") == "unveraendert"
    assert not (tmp_path / "projekt.md").exists()


def test_emit_atomically_detaches_existing_hardlink(tmp_path: Path) -> None:
    from obsidiyan.distill.emit import emit

    outside = tmp_path.parent / f"{tmp_path.name}-outside.md"
    original = '---\ncreated: 2026-01-01\nparent: "[[portfolio]]"\n---\n\n# Alt\n'
    outside.write_text(original, encoding="utf-8")
    topic = tmp_path / "ini-tech.md"
    topic.hardlink_to(outside)

    emit(
        [_claim("k", "Neu", "2026-08-01")],
        tmp_path,
        today=TODAY,
        entity_parents={"Projekt": _emit_parent(tmp_path)},
    )

    assert outside.read_text(encoding="utf-8") == original
    assert "Neu" in topic.read_text(encoding="utf-8")
    assert topic.stat().st_ino != outside.stat().st_ino


def test_emit_rejects_duplicate_parent_before_writing(tmp_path: Path) -> None:
    from obsidiyan.distill.emit import emit

    _emit_parent(tmp_path)
    hub = tmp_path / "projekt.md"
    original = (
        '---\ncreated: 2026-01-01\nparent: "[[portfolio]]"\n'
        'parent: "[[portfolio]]"\n---\n\n# Projekt\n'
    )
    hub.write_text(original, encoding="utf-8")

    with pytest.raises(ValueError, match="hoechstens einen parent"):
        emit(
            [_claim("k", "Neu", "2026-08-01")],
            tmp_path,
            today=TODAY,
            entity_parents={"Projekt": "portfolio"},
        )

    assert hub.read_text(encoding="utf-8") == original
    assert not (tmp_path / "ini-tech.md").exists()


def test_emit_rejects_case_varied_self_parent(tmp_path: Path) -> None:
    from obsidiyan.distill.emit import emit

    hub = tmp_path / "projekt.md"
    hub.write_text(
        '---\ncreated: 2026-01-01\nparent: "[[PROJEKT]]"\n---\n\n# Projekt\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="eigener parent"):
        emit([_claim("k", "Neu", "2026-08-01")], tmp_path, today=TODAY)
    assert not (tmp_path / "ini-tech.md").exists()


def test_emit_keeps_sections_appended_after_the_distill_block(tmp_path: Path) -> None:
    """Regression: emit hat an notes/teamco.md zwei Abschnitte still geloescht.

    ``remember`` haengt neue Abschnitte ans Dateiende, also hinter den
    Destillat-Block. Der alte Code nahm mit ``split(marker)[0]`` an, ab dem
    Marker gehoere die Datei ihm, und schrieb alles danach weg.
    """
    from obsidiyan.distill.emit import emit

    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "brain.md").write_text("# BRAIN\n", encoding="utf-8")
    (notes / "projekt.md").write_text(
        "---\ncreated: 2026-08-01\nmodified: 2026-08-01\nparent: \"[[notes/brain]]\"\n---\n\n"
        "# Projekt\n\nHandgeschriebener Kopf.\n\n"
        "## Destillierte Unterthemen\n\n- alter Eintrag\n\n"
        "## Von Hand nachgetragen\n\nDieser Absatz muss ueberleben.\n",
        encoding="utf-8",
    )

    emit(
        [
            Claim(
                entity="Projekt",
                topic="Projekt Thema",
                key="k",
                kind=ClaimKind.FACT,
                text="Eine belegte Aussage.",
                stated_on=date(2026, 8, 24),
                source_doc_id="egal.md",
            )
        ],
        notes,
        today=date(2026, 8, 24),
        entity_parents={"Projekt": "brain"},
    )

    body = (notes / "projekt.md").read_text(encoding="utf-8")
    assert "Handgeschriebener Kopf." in body
    assert "## Von Hand nachgetragen" in body
    assert "Dieser Absatz muss ueberleben." in body
    assert "alter Eintrag" not in body  # der Block selbst wird ersetzt
