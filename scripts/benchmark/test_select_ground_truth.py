#!/usr/bin/env python3
"""Regression tests for pinned evaluator denominator semantics."""

from __future__ import annotations

import unittest

from select_ground_truth import PINNED_DENOMINATOR_COUNTS, expected_denominators


def page(*blocks: dict[str, object]) -> dict[str, object]:
    return {"layout_dets": list(blocks), "extra": {"relation": []}}


def page_with_relations(
    blocks: list[dict[str, object]], relations: list[dict[str, object]]
) -> dict[str, object]:
    return {"layout_dets": blocks, "extra": {"relation": relations}}


class GroundTruthDenominatorTests(unittest.TestCase):
    def test_pinned_hard_reading_order_contract_is_293(self) -> None:
        self.assertEqual(PINNED_DENOMINATOR_COUNTS["hard"]["reading_order"]["Edit_dist"], 293)

    def test_pinned_full_reading_order_contract_is_1638(self) -> None:
        self.assertEqual(PINNED_DENOMINATOR_COUNTS["all"]["reading_order"]["Edit_dist"], 1638)

    def test_order_zero_without_position_is_not_a_reading_order_page(self) -> None:
        manifest = expected_denominators(
            [
                page(
                    {
                        "anno_id": 1,
                        "category_type": "text_block",
                        "text": "counted for text",
                        "order": 0,
                    }
                ),
                page(
                    {
                        "anno_id": 2,
                        "category_type": "text_block",
                        "text": "counted for reading order",
                        "order": 1,
                    }
                ),
            ],
            "synthetic",
        )
        self.assertEqual(manifest["metrics"]["text_block"]["Edit_dist"], 2)
        self.assertEqual(manifest["metrics"]["reading_order"]["Edit_dist"], 1)

    def test_truthy_position_fallback_matches_pinned_matcher(self) -> None:
        manifest = expected_denominators(
            [
                page(
                    {
                        "anno_id": 1,
                        "category_type": "equation_isolated",
                        "text": "x",
                        "order": 0,
                        "position": [7, 8],
                    }
                )
            ],
            "synthetic",
        )
        self.assertEqual(manifest["metrics"]["reading_order"]["Edit_dist"], 1)

    def test_truncated_merge_drops_original_position_like_pinned_evaluator(self) -> None:
        merged_page = page_with_relations(
            [
                {
                    "anno_id": 10,
                    "category_type": "text_block",
                    "text": "first",
                    "order": 0,
                    "position": [7, 8],
                },
                {
                    "anno_id": 11,
                    "category_type": "text_block",
                    "text": "second",
                    "order": 0,
                    "position": [9, 10],
                },
            ],
            [
                {
                    "relation_type": "truncated",
                    "source_anno_id": 10,
                    "target_anno_id": 11,
                }
            ],
        )
        manifest = expected_denominators([merged_page], "synthetic")
        self.assertEqual(manifest["metrics"]["text_block"]["Edit_dist"], 1)
        self.assertEqual(manifest["metrics"]["reading_order"]["Edit_dist"], 0)


if __name__ == "__main__":
    unittest.main()
