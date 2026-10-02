"""Claims zu Notes verdichten, deterministisch.

Kein Modell in dieser Stufe. Was hier passiert, ist Buchhaltung:

1. Claims nach Thema gruppieren
2. Innerhalb eines Themas nach `key` gruppieren. Gleicher key heisst: Aussagen
   ueber denselben Sachverhalt.
3. Pro key gewinnt der juengste datierte Claim. Aeltere, die ihm widersprechen,
   landen unter "Ueberholt" statt geloescht zu werden.
4. Jeder Punkt traegt seine Quelle.

Punkt 3 ist der Grund, warum das ueber Jahre lesbar bleibt: eine Wissensbasis
ohne Verfallsdatum wird zur Muellhalde, eine mit geloeschter Historie verliert
die Begruendung, warum sich etwas geaendert hat.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date

from obsidiyan.distill.models import Claim, ClaimKind, Confidence

_SECTION_ORDER: tuple[tuple[ClaimKind, str], ...] = (
    (ClaimKind.DECISION, "Entscheidungen"),
    (ClaimKind.FACT, "Fakten"),
    (ClaimKind.PREFERENCE, "Praeferenzen"),
    (ClaimKind.OPEN, "Offen"),
)


def _sort_key(claim: Claim) -> tuple[date, str]:
    return (claim.stated_on or date.min, claim.source_doc_id)


def resolve(claims: list[Claim]) -> tuple[list[Claim], list[tuple[Claim, Claim]]]:
    """Aktuelle Claims und die Paare (ueberholt, ersetzt_durch)."""
    buckets: dict[str, list[Claim]] = defaultdict(list)
    for claim in claims:
        buckets[claim.key].append(claim)

    current: list[Claim] = []
    superseded: list[tuple[Claim, Claim]] = []
    for group in buckets.values():
        ordered = sorted(group, key=_sort_key)
        winner = ordered[-1]
        current.append(winner)
        for older in ordered[:-1]:
            if older.text.strip() != winner.text.strip():
                superseded.append((older, winner))
    current.sort(key=_sort_key, reverse=True)
    return current, superseded


def _bullet(claim: Claim, refs: dict[str, str]) -> str:
    day = claim.stated_on.isoformat() if claim.stated_on else "ohne Datum"
    body = claim.text.strip()
    if claim.rationale.strip():
        body = f"{body}, weil {claim.rationale.strip()}"
    mark = " *(indirekt belegt)*" if claim.confidence is Confidence.MEDIUM else ""
    return f"- **{day}**: {body}{mark} [{refs[claim.source_doc_id]}]"


def render_note(
    topic: str,
    claims: list[Claim],
    today: date,
    links: list[str] | None = None,
    *,
    parent: str = "",
) -> str:
    current, superseded = resolve(claims)
    sources = sorted({c.source_doc_id for c in claims})
    refs = {src: f"Q{i}" for i, src in enumerate(sources, start=1)}
    dated = sorted(c.stated_on for c in claims if c.stated_on)
    if not dated:
        span = "ohne Datum"
    elif dated[0] == dated[-1]:
        span = dated[0].isoformat()
    else:
        span = f"{dated[0].isoformat()} bis {dated[-1].isoformat()}"
    source_label = "Quelle" if len(sources) == 1 else "Quellen"

    parent_line = f'parent: "[[{parent}]]"\n' if parent else ""
    head = (
        "---\n"
        f"created: {today.isoformat()}\n"
        f"modified: {today.isoformat()}\n"
        "ai_generated: true\n"
        f"{parent_line}"
        f"distilled_from: {len(sources)} {source_label}, {span}\n"
        "---\n\n"
        f"# {topic}\n\n"
    )
    if links:
        head += "Verwandt: " + ", ".join(f"[[{link}]]" for link in links) + "\n\n"
    head += (
        "> Destillat aus dem Corpus. Jeder Punkt traegt seine Quelle. "
        "*(indirekt belegt)* heisst: die Aussage stammt aus einer "
        "Maschinen-Zusammenfassung oder aus fremdem Referenzmaterial, "
        "nicht aus deinen woertlichen Worten. "
        "Ungepruefte Maschinenarbeit, bis du sie bestaetigst.\n"
    )

    parts = [head]
    for kind, heading in _SECTION_ORDER:
        rows = [c for c in current if c.kind is kind]
        if rows:
            parts.append(
                f"\n## {heading}\n\n" + "\n".join(_bullet(c, refs) for c in rows) + "\n"
            )

    if superseded:
        parts.append("\n## Ueberholt\n\n")
        for old, new in sorted(superseded, key=lambda p: _sort_key(p[0]), reverse=True):
            old_day = old.stated_on.isoformat() if old.stated_on else "ohne Datum"
            new_day = new.stated_on.isoformat() if new.stated_on else "ohne Datum"
            parts.append(
                f"- **{old_day}**: {old.text.strip()} [{refs[old.source_doc_id]}]\n"
                f"  ersetzt am {new_day} durch: {new.text.strip()} "
                f"[{refs[new.source_doc_id]}]\n"
            )

    parts.append("\n## Quellen\n\n")
    for src, ref in refs.items():
        parts.append(f"- **{ref}** `corpus/{src}`\n")
    return "".join(parts)
