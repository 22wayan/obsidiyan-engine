#!/usr/bin/env python
"""Read-only NDA-Audit gegen den ECHTEN Corpus.

Kein Unit-Test, weil er auf privaten Daten laeuft. Jederzeit wiederholbar,
mutiert nichts. Exit 0 nur wenn kein Leck gefunden wird.

Geprueft wird die Eigenschaft, auf die es ankommt: Kein Dokument, auf das die
zentrale NDA-/Privacy-Policy trifft, darf als clean im Corpus liegen oder ohne
expliziten Scope aus der Suche zurueckkommen.
"""

from __future__ import annotations

import subprocess
import sys

from obsidiyan import paths
from obsidiyan.corpusio import iter_docs, read_doc
from obsidiyan.models import Sensitivity
from obsidiyan.nda import (
    classify,
    load_source_overrides,
    source_is_denied,
    text_has_secret,
)
from obsidiyan.provenance import distill_provenance_errors

REPO_ROOT = paths.vault_root()
CORPUS = REPO_ROOT / "corpus"
PRIVATE = REPO_ROOT / "private"
SOURCE_OVERRIDES = load_source_overrides(paths.source_overrides_path(), required=False)


def _clean_layer_errors() -> list[str]:
    errors: list[str] = []
    candidates = [
        REPO_ROOT / "BRAIN.md",
        REPO_ROOT / "distill" / "claims.json",
        REPO_ROOT / "distill" / "reviews.json",
    ]
    for folder in (REPO_ROOT / "notes", REPO_ROOT / "memory"):
        if folder.exists():
            candidates.extend(sorted(folder.rglob("*.md")))

    for path in candidates:
        if not path.is_file():
            continue
        body = path.read_text(encoding="utf-8", errors="replace")
        if classify(str(path.relative_to(REPO_ROOT)), body) is Sensitivity.NDA:
            rel = path.relative_to(REPO_ROOT)
            errors.append(f"Clean-Layer {rel}: zentrale NDA-/Privacy-Policy trifft")
    return errors


def _private_layer_errors(corpus_paths: set[str]) -> list[str]:
    errors: list[str] = []
    private_notes = sorted(PRIVATE.rglob("*.md")) if PRIVATE.exists() else []
    if not private_notes:
        errors.append("private/ enthaelt keine Markdown-Notes")
        return errors

    listing = subprocess.run(
        ["git", "ls-files", "--", "private"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    # Ein Vault ohne git hat nichts getrackt; nur ein echtes Repo kann lecken.
    tracked = listing.stdout.strip() if listing.returncode == 0 else ""
    if tracked:
        errors.append(f"private/ ist teilweise getrackt: {tracked}")

    for path in private_notes:
        relative = path.relative_to(REPO_ROOT)
        body = path.read_text(encoding="utf-8", errors="replace")
        if text_has_secret(body):
            errors.append(f"moegliches Secret im privaten Layer: {relative}")
        ignored = subprocess.run(
            ["git", "check-ignore", "-q", str(relative)],
            cwd=REPO_ROOT,
            check=False,
        )
        if ignored.returncode != 0:
            errors.append(f"nicht gitignored: {relative}")
        if str(relative) not in corpus_paths:
            errors.append(f"private Note fehlt im Corpus: {relative}")
    return errors


def main() -> int:
    if not CORPUS.exists():
        print("corpus/ missing, run an ingest first", file=sys.stderr)
        return 1

    total = clean = leaks = private_docs = copyright_docs = 0
    corpus_paths: set[str] = set()
    for path in iter_docs(CORPUS):
        try:
            doc = read_doc(path)
        except (ValueError, KeyError) as exc:
            print(f"UNREADABLE {path.name}: {exc}", file=sys.stderr)
            return 1
        total += 1
        corpus_paths.add(doc.conv_id)
        if doc.conv_id.startswith("private/"):
            private_docs += 1
            if doc.sensitivity is not Sensitivity.NDA:
                leaks += 1
                print(f"LEAK {path.name}: private note not confidential", file=sys.stderr)
        if doc.sensitivity is Sensitivity.COPYRIGHT:
            # Fremdmaterial wird bewusst nicht gegen die Clean-Policy geprueft.
            # Die Policy schuetzt den git-getrackten Layer und die Destillation,
            # und aus beidem ist COPYRIGHT strukturell ausgeschlossen. Ein
            # Dozenten-Postfach oder ein Beispiel-API-Key in einem
            # Kurstranskript waere sonst ein rotes Gate ohne Schutzwirkung.
            copyright_docs += 1
        elif doc.sensitivity is not Sensitivity.NDA:
            clean += 1
            body = "\n".join(t.text for t in doc.turns)
            if (
                classify(doc.project, body) is Sensitivity.NDA
                or source_is_denied(doc.source.value, doc.conv_id, SOURCE_OVERRIDES)
            ):
                leaks += 1
                print(f"LEAK {path.name}: clean doc matches NDA policy", file=sys.stderr)

    # Die git-getrackten Stores duerfen ausschliesslich clean-Quellen
    # referenzieren. Sonst schiebt der Auto-Distill-PR vertraulichen Claim-Text
    # nach GitHub. Die gitignorierten Zwillinge duerfen NDA, aber kein
    # urheberrechtlich geschuetztes Fremdmaterial.
    provenance_errors = distill_provenance_errors(
        CORPUS,
        {
            REPO_ROOT / "distill" / "claims.json": "source_doc_id",
            REPO_ROOT / "distill" / "reviews.json": "doc_id",
        },
        require_clean=True,
    )
    private_stores = {
        path: field
        for path, field in (
            (REPO_ROOT / "distill" / "claims-private.json", "source_doc_id"),
            (REPO_ROOT / "distill" / "reviews-private.json", "doc_id"),
        )
        if path.is_file()
    }
    if private_stores:
        provenance_errors += distill_provenance_errors(CORPUS, private_stores)
    layer_errors = [
        *_private_layer_errors(corpus_paths),
        *_clean_layer_errors(),
        *provenance_errors,
    ]
    for error in layer_errors:
        print(f"LEAK {error}", file=sys.stderr)

    print(
        f"docs={total} clean={clean} copyright={copyright_docs} "
        f"nda={total - clean - copyright_docs} "
        f"private={private_docs} leaks={leaks + len(layer_errors)}"
    )
    if leaks or layer_errors:
        print("FAIL: confidential material would reach the committed layer", file=sys.stderr)
        return 1
    print("PASS: no clean document violates the NDA/privacy policy")
    return 0


if __name__ == "__main__":
    sys.exit(main())
