"""CLI: python -m obsidiyan ingest|search|stats"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path

from obsidiyan import ingest, paths
from obsidiyan.corpusio import iter_docs, read_doc
from obsidiyan.models import Sensitivity

DEFAULT_CORPUS = paths.corpus_root()


def _ranking_from_env() -> str:
    value = os.environ.get("OBSIDIYAN_RANKING", "fused")
    if value not in ("fused", "hybrid", "substring"):
        raise SystemExit(f"OBSIDIYAN_RANKING must be fused, hybrid or substring, not {value!r}")
    return value


def _join_query(terms: list[str]) -> str:
    """Shell-Argumente zur Suchsyntax zusammensetzen.

    Die Shell entfernt die Anfuehrungszeichen um eine Wortfolge. Ein Argument
    mit Leerzeichen war deshalb eine Phrase und bekommt sie zurueck.
    """
    return " ".join(f'"{term}"' if " " in term else term for term in terms)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="obsidiyan",
        description="Shared, sourced memory for AI coding agents. "
        "Set OBSIDIYAN_HOME to your vault.",
    )
    parser.add_argument(
        "--corpus",
        type=Path,
        default=DEFAULT_CORPUS,
        help="corpus folder (default: $OBSIDIYAN_HOME/corpus)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    ing = sub.add_parser("ingest", help="turn sessions or chat exports into corpus documents")
    ing.add_argument(
        "--source", required=True, choices=sorted(ingest.ADAPTERS), help="which adapter to run"
    )
    ing.add_argument("--dry-run", action="store_true", help="parse and count, write nothing")
    ing.add_argument("--limit", type=int, default=None, help="stop after N conversations")
    ing.add_argument(
        "--session-root",
        type=Path,
        default=None,
        help="read sessions from this folder instead of the default location",
    )
    ing.add_argument(
        "--require-overrides",
        action="store_true",
        help="abort if private/nda-source-overrides.json is missing or empty",
    )

    emb = sub.add_parser(
        "embed", help='build or refresh the local embedding index (needs ".[embeddings]")'
    )
    emb.add_argument("--model", default=None, help="sentence-transformers model id")

    init = sub.add_parser("init", help="create a new vault (BRAIN.md, hub notes, private/)")
    init.add_argument(
        "path",
        nargs="?",
        type=Path,
        default=None,
        help="vault directory; defaults to $OBSIDIYAN_HOME or ./vault",
    )
    init.add_argument("--demo", action="store_true", help="add and ingest demo conversations")

    sea = sub.add_parser(
        "search",
        help='search the corpus; documents with every term rank first, "..." must occur',
    )
    sea.add_argument(
        "query",
        nargs="+",
        help='search terms, e.g. postgres "managed backups"',
    )
    sea.add_argument("--limit", type=int, default=10, help="maximum number of hits")
    sea.add_argument(
        "--include-nda", action="store_true", help="also return confidential documents"
    )
    sea.add_argument(
        "--since", type=date.fromisoformat, default=None, help="only documents from YYYY-MM-DD on"
    )
    sea.add_argument(
        "--ranking",
        choices=["fused", "hybrid", "substring"],
        default=_ranking_from_env(),
        help="fused: BM25 plus exact matches (default); hybrid: plus local embeddings",
    )
    sea.add_argument("--source", default=None, help="only this source, e.g. claude-code")
    sea.add_argument("--project", default=None, help="only this project")

    sub.add_parser("stats", help="count corpus documents per source")

    dis = sub.add_parser("distill", help="list conversations worth distilling into claims")
    dis.add_argument("--limit", type=int, default=25, help="maximum number of candidates")
    dis.add_argument(
        "--min-chars", type=int, default=2000, help="minimum user text per conversation"
    )
    dis.add_argument("--project", default=None, help="only this project")
    dis.add_argument("--pending", action="store_true", help="skip candidates that are already done")

    show = sub.add_parser("show", help="print the user turns of one candidate")
    show.add_argument("doc_id", help="doc_id from 'distill', or part of it")
    show.add_argument("--min-chars", type=int, default=2000)

    # --min-chars muss zu dem Lauf passen, aus dem die Claims stammen. Die
    # Schwelle ordnet die Warteschlange, sie entscheidet nicht ueber die
    # Gueltigkeit eines Claims: wer bewusst tiefer auswaehlt, muss den Ertrag
    # auch ablegen koennen. Der Default bleibt streng.
    add = sub.add_parser("add-claims", help="add claims from a JSON file to the claim store")
    add.add_argument("json_file", type=Path)
    add.add_argument("--min-chars", type=int, default=2000)

    review = sub.add_parser(
        "add-reviews", help="mark candidates as done without claims (JSON file)"
    )
    review.add_argument("json_file", type=Path)
    review.add_argument("--min-chars", type=int, default=2000)

    emit_parser = sub.add_parser("emit", help="rewrite all generated notes from the claim store")
    emit_parser.add_argument(
        "--entity-parent",
        action="append",
        default=[],
        metavar="ENTITY=PARENT",
        help="parent note for a new entity hub; repeat for several entities",
    )
    sub.add_parser("progress", help="show distillation progress")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "ingest":
        stats = ingest.run(
            args.source,
            args.corpus,
            session_root=args.session_root,
            dry_run=args.dry_run,
            limit=args.limit,
            require_source_overrides=args.require_overrides,
        )
        prefix = "[dry-run] " if args.dry_run else ""
        print(f"{prefix}{args.source}: {stats.render()}")
        return 0

    if args.command == "init":
        return _init_command(args)

    if args.command == "embed":
        from obsidiyan import dense

        model = args.model or dense.current_model()
        if args.model and args.model != dense.current_model():
            print(
                f"note: search uses {dense.current_model()}; set OBSIDIYAN_EMBED_MODEL={model} "
                "so it uses this index",
                file=sys.stderr,
            )
        try:
            encoder = dense.sentence_transformer_encoder(model)
        except dense.EmbeddingsUnavailable as exc:
            print(exc, file=sys.stderr)
            return 1

        def report(done: int, total: int) -> None:
            print(f"\r{done}/{total} documents encoded", end="", file=sys.stderr, flush=True)

        encoded, total = dense.update_index(args.corpus, encoder, model=model, progress=report)
        print(f"\n{encoded} of {total} documents encoded, the rest reused", file=sys.stderr)
        return 0

    if args.command == "search":
        from obsidiyan.dense import EmbeddingsUnavailable
        from obsidiyan.search import _ripgrep_binary, search

        if _ripgrep_binary() is None:
            print(
                "hint: ripgrep (rg) not found, search uses a slower Python scan. "
                "Install it, e.g. `brew install ripgrep` or `apt install ripgrep`.",
                file=sys.stderr,
            )

        options = {
            "limit": args.limit,
            "include_nda": args.include_nda,
            "since": args.since,
            "source": args.source,
            "project": args.project,
        }
        try:
            hits = search(_join_query(args.query), args.corpus, ranking=args.ranking, **options)
        except EmbeddingsUnavailable as exc:
            print(f"hint: {exc}; falling back to ranking=fused", file=sys.stderr)
            hits = search(_join_query(args.query), args.corpus, ranking="fused", **options)
        if not hits:
            print("no results")
            return 0
        for hit in hits:
            print(hit.render())
            print()
        return 0

    if args.command in ("show", "add-claims", "add-reviews", "emit", "progress"):
        return _distill_command(args)

    if args.command == "distill":
        from obsidiyan.distill.select import select
        from obsidiyan.distill.store import (
            DEFAULT_PRIVATE_REVIEW_PATH,
            DEFAULT_REVIEW_PATH,
            ReviewStore,
        )

        kw = {"min_user_chars": args.min_chars, "project": args.project}
        if args.pending:
            candidates = select(args.corpus, **kw)
            fingerprints = {
                candidate.doc_id: candidate.source_fingerprint
                for candidate in candidates
            }
            done = ReviewStore(DEFAULT_REVIEW_PATH).current_sources(
                fingerprints
            ) | ReviewStore(DEFAULT_PRIVATE_REVIEW_PATH).current_sources(fingerprints)
            picked = [
                candidate for candidate in candidates if candidate.doc_id not in done
            ][: args.limit]
        else:
            picked = select(args.corpus, limit=args.limit, **kw)
        print(f"{len(picked)} Kandidaten (Score = Nutzertext x Aktualitaet)\n")
        for cand in picked:
            print(cand.render())
        return 0

    total = nda = 0
    per_source: dict[str, int] = {}
    for path in iter_docs(args.corpus):
        try:
            doc = read_doc(path)
        except (ValueError, KeyError):
            continue
        total += 1
        per_source[doc.source.value] = per_source.get(doc.source.value, 0) + 1
        if doc.sensitivity is Sensitivity.NDA:
            nda += 1
    print(f"docs={total} clean={total - nda} nda={nda}")
    for name, count in sorted(per_source.items()):
        print(f"  {name:14} {count}")
    return 0


def _init_command(args: argparse.Namespace) -> int:
    import os

    from obsidiyan.vault_init import init_vault

    target = args.path or Path(os.environ.get(paths.ENV_VAR) or "vault")
    result = init_vault(target, demo=args.demo)
    print(f"vault: {result.root}")
    for path in result.created:
        print(f"  created {path.relative_to(result.root)}")
    if result.git_initialized:
        print("  initialised a git repository (private/ and corpus/ are ignored)")
    if args.demo:
        print(f"  demo: {result.demo_docs} conversations ingested")
    print(f"\nNext: export {paths.ENV_VAR}={result.root}")
    return 0


def _distill_command(args: argparse.Namespace) -> int:
    """Destillation: zeigen, Claims/Reviews ablegen, schreiben und zaehlen."""
    import json

    from obsidiyan.distill.emit import emit
    from obsidiyan.distill.models import Claim, ReviewReason, SourceReview
    from obsidiyan.distill.route import RoutingError, route, split_doc_ids
    from obsidiyan.distill.select import select, user_text
    from obsidiyan.distill.store import (
        DEFAULT_PATH,
        DEFAULT_PRIVATE_PATH,
        DEFAULT_PRIVATE_REVIEW_PATH,
        DEFAULT_REVIEW_PATH,
        ClaimStore,
        ReviewStore,
    )

    store = ClaimStore(DEFAULT_PATH)
    reviews = ReviewStore(DEFAULT_REVIEW_PATH)
    private_store = ClaimStore(DEFAULT_PRIVATE_PATH)
    private_reviews = ReviewStore(DEFAULT_PRIVATE_REVIEW_PATH)
    notes_root = paths.notes_root()
    private_root = paths.private_root()

    if args.command == "show":
        matches = [
            c
            for c in select(args.corpus, min_user_chars=args.min_chars)
            if args.doc_id in c.doc_id
        ]
        if not matches:
            print(f"kein Kandidat passt auf {args.doc_id!r}")
            return 1
        cand = matches[0]
        print(f"# {cand.doc_id}\n# {cand.started_at} | Prosa {cand.prose:.0%}\n")
        print(user_text(cand.path, max_chars=200_000))
        return 0

    if args.command == "add-claims":
        rows = json.loads(args.json_file.read_text(encoding="utf-8"))
        claims = [Claim.model_validate(r) for r in rows]
        candidate_by_id = {
            candidate.doc_id: candidate
            for candidate in select(args.corpus, min_user_chars=args.min_chars)
        }
        source_ids = {claim.source_doc_id for claim in claims}
        unknown = sorted(source_ids - candidate_by_id.keys())
        if unknown:
            print(
                "Claims abgelehnt; Quellen sind keine aktuellen Kandidaten: "
                + ", ".join(unknown)
            )
            return 1
        try:
            routed = route(claims, args.corpus)
        except RoutingError as exc:
            print(f"Claims abgelehnt: {exc}")
            return 1
        new, replaced = store.add(routed.clean)
        store.save()
        private_new, private_replaced = private_store.add(routed.private)
        private_store.save()
        new += private_new
        replaced += private_replaced
        completions = [
            SourceReview(
                doc_id=doc_id,
                source_fingerprint=candidate_by_id[doc_id].source_fingerprint,
                reason=ReviewReason.CLAIMS_EXTRACTED,
                reviewed_on=date.today(),
            )
            for doc_id in sorted(source_ids)
        ]
        clean_ids, private_ids = split_doc_ids(sorted(source_ids), args.corpus)
        by_id = {c.doc_id: c for c in completions}
        review_new, review_replaced = reviews.add([by_id[i] for i in clean_ids])
        reviews.save()
        pr_new, pr_replaced = private_reviews.add([by_id[i] for i in private_ids])
        private_reviews.save()
        review_new += pr_new
        review_replaced += pr_replaced
        print(
            f"{len(routed.clean)} clean, {len(routed.private)} privat; "
            f"{new} neu, {replaced} ersetzt, "
            f"{len(store.all()) + len(private_store.all())} gesamt; "
            f"Completion {review_new} neu/{review_replaced} aktualisiert"
        )
        return 0

    if args.command == "add-reviews":
        rows = json.loads(args.json_file.read_text(encoding="utf-8"))
        incoming = [SourceReview.model_validate(row) for row in rows]
        candidate_by_id = {
            candidate.doc_id: candidate
            for candidate in select(args.corpus, min_user_chars=args.min_chars)
        }
        unknown = sorted(
            review.doc_id for review in incoming if review.doc_id not in candidate_by_id
        )
        if unknown:
            print("Reviews abgelehnt; keine aktuellen Kandidaten: " + ", ".join(unknown))
            return 1
        claim_sources = store.sources() | private_store.sources()
        wrong_outcome = sorted(
            review.doc_id
            for review in incoming
            if (review.doc_id in claim_sources)
            != (review.reason is ReviewReason.CLAIMS_EXTRACTED)
        )
        if wrong_outcome:
            print(
                "Reviews abgelehnt; claims_extracted passt nicht zum Claim-Store: "
                + ", ".join(wrong_outcome)
            )
            return 1
        incoming = [
            review.model_copy(
                update={
                    "source_fingerprint": candidate_by_id[
                        review.doc_id
                    ].source_fingerprint
                }
            )
            for review in incoming
        ]
        try:
            clean_ids, private_ids = split_doc_ids(
                [review.doc_id for review in incoming], args.corpus
            )
        except RoutingError as exc:
            print(f"Reviews abgelehnt: {exc}")
            return 1
        by_id = {review.doc_id: review for review in incoming}
        new, replaced = reviews.add([by_id[i] for i in clean_ids])
        reviews.save()
        pr_new, pr_replaced = private_reviews.add([by_id[i] for i in private_ids])
        private_reviews.save()
        print(
            f"{new + pr_new} neu, {replaced + pr_replaced} ersetzt, "
            f"{len(reviews.all())} clean und {len(private_reviews.all())} private Reviews"
        )
        return 0

    if args.command == "emit":
        entity_parents: dict[str, str] = {}
        for assignment in args.entity_parent:
            entity, separator, parent = assignment.partition("=")
            if not separator or not entity.strip() or not parent.strip():
                print(f"ungueltiges --entity-parent {assignment!r}; erwartet ENTITY=PARENT")
                return 1
            entity_parents[entity.strip()] = parent.strip()
        # Zwei Laeufe statt einem: die Stufe der Quelle entscheidet, in welchen
        # Layer das Destillat geht. route() liest sie aus dem Corpus, damit ein
        # Vertipper im Claim die Layer-Grenze nicht oeffnen kann.
        try:
            routed = route(store.all() + private_store.all(), args.corpus)
        except RoutingError as exc:
            print(f"emit abgelehnt: {exc}")
            return 1

        written: list[tuple[Path, bool]] = []
        try:
            if routed.clean:
                written += emit(routed.clean, notes_root, entity_parents=entity_parents)
            if routed.private:
                written += emit(
                    routed.private,
                    private_root,
                    entity_parents=entity_parents,
                    private=True,
                )
        except ValueError as exc:
            print(f"emit abgelehnt: {exc}")
            return 1
        for path, created in written:
            print(("NEU  " if created else "ERG  ") + str(path))
        # Frisch geschriebene Notes muessen sofort im Corpus stehen. verify-nda
        # verlangt fuer jede private Note ein Corpus-Dokument, und der
        # unbeaufsichtigte Lauf prueft das Gate direkt nach dem Emit. Ohne
        # diesen Schritt war er rot, obwohl nichts kaputt war.
        if written:
            ingest.run("memory", args.corpus, require_source_overrides=False)
        print(f"{len(routed.clean)} Claims nach notes/, {len(routed.private)} nach private/")
        return 0

    candidate_list = select(args.corpus)
    candidate_ids = {candidate.doc_id for candidate in candidate_list}
    fingerprints = {
        candidate.doc_id: candidate.source_fingerprint for candidate in candidate_list
    }
    completed_sources = reviews.current_sources(fingerprints) | private_reviews.current_sources(
        fingerprints
    )
    claimed_sources = store.sources() | private_store.sources()
    extracted = len(candidate_ids & completed_sources & claimed_sources)
    reviewed = len((candidate_ids & completed_sources) - claimed_sources)
    offen = [
        candidate
        for candidate in candidate_list
        if candidate.doc_id not in completed_sources
    ]
    gesamt = len(candidate_list)
    print(f"Kandidaten gesamt : {gesamt}")
    print(f"extrahiert        : {extracted}")
    print(f"ohne Claims gepr. : {reviewed}")
    print(f"offen             : {len(offen)}")
    print(f"Claims im Store   : {len(store.all())} clean, "
          f"{len(private_store.all())} privat")
    for topic, count in store.topics().items():
        print(f"   {count:4}  {topic}")
    return 0
