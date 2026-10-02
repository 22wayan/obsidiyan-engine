from __future__ import annotations

import json
import os
from pathlib import Path

from obsidiyan.watermark import Watermark


def test_same_policy_reuses_fingerprint(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("inhalt", encoding="utf-8")
    mark_path = tmp_path / "watermark.json"

    mark = Watermark(mark_path, policy_version="policy-a")
    assert mark.is_fresh(source)
    mark.mark(source)
    mark.save()

    loaded = Watermark(mark_path, policy_version="policy-a")
    assert not loaded.is_fresh(source)


def test_changed_policy_invalidates_all_fingerprints(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("inhalt", encoding="utf-8")
    mark_path = tmp_path / "watermark.json"
    first = Watermark(mark_path, policy_version="policy-a")
    first.mark(source)
    first.save()

    assert Watermark(mark_path, policy_version="policy-b").is_fresh(source)


def test_legacy_watermark_is_invalidated(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("inhalt", encoding="utf-8")
    mark_path = tmp_path / "watermark.json"
    mark_path.write_text(
        json.dumps({str(source): list(Watermark.fingerprint(source))}),
        encoding="utf-8",
    )

    assert Watermark(mark_path, policy_version="policy-a").is_fresh(source)


def test_wrapped_v2_watermark_is_invalidated(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("inhalt", encoding="utf-8")
    mark_path = tmp_path / "watermark.json"
    mark_path.write_text(
        json.dumps(
            {
                "format_version": 2,
                "policy_version": "policy-a",
                "files": {str(source): list(Watermark.fingerprint(source))},
            }
        ),
        encoding="utf-8",
    )

    assert Watermark(mark_path, policy_version="policy-a").is_fresh(source)


def test_same_size_change_within_same_second_is_detected(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("aaaa", encoding="utf-8")
    first_ns = 1_800_000_000_100_000_000
    os.utime(source, ns=(first_ns, first_ns))
    mark = Watermark(tmp_path / "watermark.json", policy_version="policy-a")
    mark.mark(source)

    source.write_text("bbbb", encoding="utf-8")
    second_ns = first_ns + 1
    os.utime(source, ns=(second_ns, second_ns))

    assert mark.is_fresh(source)
