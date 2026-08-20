#!/usr/bin/env python3
"""Negative smoke for restored source-cache byte verification."""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from prepare_sources import corpus_manifest, load_index, verify


class RestoredSourceVerificationTests(unittest.TestCase):
    def fixture(self, root: Path) -> argparse.Namespace:
        index = root / "index.json"
        output = root / "corpus"
        source = output / "sources" / "demo" / "page.png"
        source.parent.mkdir(parents=True)
        payload = b"official-raw-image-bytes"
        source.write_bytes(payload)
        item = {
            "page_info": {
                "image_path": "demo/page.png",
                "page_attribute": {"subset": "layout_hard"},
            },
            "source_bytes": len(payload),
            "source_sha256": hashlib.sha256(payload).hexdigest(),
        }
        index.write_text(json.dumps([item]) + "\n", encoding="utf-8")
        (output / "source-index.json").write_bytes(index.read_bytes())
        selected = load_index(index, "hard", 0, 1)
        (output / "corpus-manifest.json").write_text(
            json.dumps(corpus_manifest(index, "hard", 0, 1, selected), indent=2) + "\n",
            encoding="utf-8",
        )
        return argparse.Namespace(
            index=index,
            output=output,
            subset="hard",
            shard_index=0,
            shard_count=1,
            workers=1,
        )

    def test_valid_cache_is_verified(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            args = self.fixture(Path(directory))
            result = verify(args)
            self.assertTrue(result["valid"])
            self.assertEqual(result["file_count"], 1)

    def test_tampered_cached_source_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            args = self.fixture(Path(directory))
            (args.output / "sources" / "demo" / "page.png").write_bytes(b"tampered")
            with self.assertRaisesRegex(RuntimeError, "source cache integrity mismatch"):
                verify(args)

    def test_extra_cached_source_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            args = self.fixture(Path(directory))
            (args.output / "sources" / "unexpected.jpg").write_bytes(b"extra")
            with self.assertRaisesRegex(RuntimeError, "source cache set mismatch"):
                verify(args)


if __name__ == "__main__":
    unittest.main()
