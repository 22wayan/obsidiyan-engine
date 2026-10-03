from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from conftest import make_doc

from obsidiyan.corpusio import write_doc
from obsidiyan.models import Sensitivity
from obsidiyan.nda import (
    DENY_SLUGS,
    DENY_TERMS,
    classify,
    load_source_overrides,
    policy_signature,
    redact_secrets,
    source_is_denied,
    sync_memory_deny_list,
    sync_memory_deny_terms,
    text_has_email,
    text_has_secret,
)
from obsidiyan.search import search


def test_denied_project_slug_is_nda() -> None:
    assert classify("CRM-globex", "harmloser Text") is Sensitivity.NDA
    assert classify("CRM_globex", "harmloser Text") is Sensitivity.NDA
    assert classify("acme-poc-2", "harmloser Text") is Sensitivity.NDA
    assert classify("ini-tech", "harmloser Text") is Sensitivity.NDA
    assert classify("Umbrella-Energie", "harmloser Text") is Sensitivity.NDA
    assert classify("hooli-mag-website", "harmloser Text") is Sensitivity.NDA
    assert classify("Globex-GmbH", "harmloser Text") is Sensitivity.NDA
    assert classify("final-pr-acme", "harmloser Text") is Sensitivity.NDA


def test_denied_term_in_text_is_nda() -> None:
    assert classify("neutrales-projekt", "wir haben mit Acme gesprochen") is Sensitivity.NDA
    assert classify("neutrales-projekt", "Termin mit ini.tech") is Sensitivity.NDA
    assert classify("neutrales-projekt", "STORYBOT Prototyp") is Sensitivity.NDA


def test_denied_entity_in_project_name_is_nda() -> None:
    assert classify("archiv-globex-alt", "neutral") is Sensitivity.NDA


def test_email_and_secret_are_private() -> None:
    assert classify("neutral", "Kontakt test@example.com") is Sensitivity.NDA
    assert classify("neutral", "Token sk-" + "a" * 24) is Sensitivity.NDA


def test_similar_word_is_not_flagged() -> None:
    """Wortgrenzen: 'Acmeware' ist nicht 'Acme'."""
    assert classify("neutrales-projekt", "Acmeware ist ein anderes Produkt") is Sensitivity.CLEAN
    assert classify("neutrales-projekt", "iniXtech ist nur ein Test") is Sensitivity.CLEAN


def test_clean_stays_clean() -> None:
    assert classify("polyglot-app", "FSRS und Vite") is Sensitivity.CLEAN


def test_local_source_override_is_private_without_sensitive_terms(tmp_path: Path) -> None:
    path = tmp_path / "overrides.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "sources": [{"source": "gemini", "conv_id": "opaque-17"}],
            }
        ),
        encoding="utf-8",
    )

    overrides = load_source_overrides(path)

    assert source_is_denied("gemini", "opaque-17", overrides)
    assert not source_is_denied("gemini", "opaque-18", overrides)
    assert policy_signature(overrides) != policy_signature()


def test_malformed_source_override_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "overrides.json"
    path.write_text('{"version": 1, "sources": [{"source": "gemini"}]}', encoding="utf-8")

    with pytest.raises(ValueError, match="conv_id"):
        load_source_overrides(path)

    path.write_text(
        '{"version": 1, "sources": [{"source": "typo", "conv_id": "opaque"}]}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unbekannte source"):
        load_source_overrides(path)


def test_required_source_override_must_exist(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="overrides missing"):
        load_source_overrides(tmp_path / "missing.json", required=True)

    empty = tmp_path / "empty.json"
    empty.write_text('{"version": 1, "sources": []}', encoding="utf-8")
    with pytest.raises(ValueError, match="overrides are empty"):
        load_source_overrides(empty, required=True)


def test_deny_list_matches_sync_memory_script(repo_root: Path) -> None:
    """Drift-Schutz: bin/sync-memory.sh ist die zweite Kopie derselben Liste."""
    assert set(sync_memory_deny_list(repo_root)) == set(DENY_SLUGS)


def test_deny_terms_match_sync_memory_script(repo_root: Path) -> None:
    assert set(sync_memory_deny_terms(repo_root)) == set(DENY_TERMS)


def test_gitignore_catches_every_denied_memory_slug(repo_root: Path) -> None:
    for slug in DENY_SLUGS:
        result = subprocess.run(
            ["git", "check-ignore", "--no-index", "-q", f"memory/{slug}/MEMORY.md"],
            cwd=repo_root,
            check=False,
        )
        assert result.returncode == 0, slug

        case_variant = slug.swapcase()
        result = subprocess.run(
            [
                "git",
                "check-ignore",
                "--no-index",
                "-q",
                f"memory/{case_variant}/MEMORY.md",
            ],
            cwd=repo_root,
            check=False,
        )
        assert result.returncode == 0, case_variant


def test_nda_doc_is_invisible_without_scope(tmp_path: Path) -> None:
    """Der negative Test: geflaggtes Material darf ohne expliziten Scope nicht zurueckkommen."""
    write_doc(make_doc(conv_id="a", sensitivity=Sensitivity.NDA), tmp_path)
    assert search("Entscheidung", tmp_path) == []


def test_nda_doc_is_visible_with_explicit_scope(tmp_path: Path) -> None:
    write_doc(make_doc(conv_id="a", sensitivity=Sensitivity.NDA), tmp_path)
    hits = search("Entscheidung", tmp_path, include_nda=True)
    assert len(hits) == 1
    assert hits[0].doc.sensitivity is Sensitivity.NDA


def test_sk_inside_a_word_is_not_a_secret() -> None:
    assert not text_has_secret("Run the task-runner-configuration-check before the deploy.")
    assert text_has_secret("key: sk-" + "proj" + "Q" * 24)


def test_retina_asset_names_are_not_email_addresses() -> None:
    assert not text_has_email("Export the logo as logo@2x.png for retina screens.")
    assert text_has_email("Send it to jane.doe@example.com")


# Fake values are assembled at runtime so secret scanners do not flag the repo.
FAKE_21ST = "an_" + "sk_" + "a1B2c3D4" * 4
FAKE_STRIPE_LIVE = "sk_" + "live_" + "Z9y8X7w6" * 3
FAKE_SLACK = "xox" + "b-" + "1234567890-" + "abcDEF" * 4


def test_detects_more_key_formats() -> None:
    assert text_has_secret(f"API_KEY = \"{FAKE_21ST}\"")
    assert text_has_secret(f"stripe key {FAKE_STRIPE_LIVE}")
    assert text_has_secret(f"bot token {FAKE_SLACK}")


def test_detects_generic_key_assignments_with_long_values() -> None:
    assert text_has_secret("MAGIC_API_KEY=" + "q7Lm2Xr9Tb4Vn8Kc1Hp6Wd3Zs5")
    assert text_has_secret("password: " + "Tr0ub4dor-and-3-horses-staple")


def test_ignores_key_names_without_a_secret_value() -> None:
    assert not text_has_secret("API_KEY=process.env.ANTHROPIC_API_KEY")
    assert not text_has_secret("Set OPENAI_API_KEY to the key from your dashboard.")
    assert not text_has_secret("TOKEN_EXPIRY_SECONDS = 3600")


def test_redact_secrets_removes_the_value_and_keeps_the_text() -> None:
    text = f"In config.toml steht API_KEY = \"{FAKE_21ST}\", bitte rotieren."
    redacted = redact_secrets(text)
    assert FAKE_21ST not in redacted
    assert "[secret removed]" in redacted
    assert redacted.startswith("In config.toml steht")
    assert redact_secrets("nothing to see") == "nothing to see"
