#!/usr/bin/env python3
"""Materialize the official evaluator slice after source inference has ended."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

HARD_SUBSETS = {"equation_hard", "layout_hard", "table_hard"}
TEXT_CATEGORIES = {"text_block", "title", "code_txt", "code_txt_caption", "reference"}


def raw_page_categories(item: dict[str, Any]) -> set[str]:
    return {
        str(block["category_type"])
        for block in item.get("layout_dets", [])
        if not block.get("ignore", False) and block.get("category_type")
    }


def matched_page_categories(item: dict[str, Any]) -> set[str]:
    groups: list[set[object]] = []
    for relation in item.get("extra", {}).get("relation", []):
        if relation.get("relation_type") != "truncated":
            continue
        merged = {relation.get("source_anno_id"), relation.get("target_anno_id")}
        untouched = []
        for group in groups:
            if group & merged:
                merged |= group
            else:
                untouched.append(group)
        groups = [*untouched, merged]
    truncated_ids = set().union(*groups) if groups else set()
    truncated_blocks: dict[object, dict[str, Any]] = {}
    categories = set()
    for block in item.get("layout_dets", []):
        annotation_id = block.get("anno_id")
        if annotation_id in truncated_ids:
            truncated_blocks[annotation_id] = block
        elif not block.get("ignore", False) and block.get("category_type"):
            categories.add(str(block["category_type"]))
    for group in groups:
        blocks = [truncated_blocks[key] for key in group if key in truncated_blocks]
        if not blocks or any(block.get("ignore", False) for block in blocks):
            continue
        first = min(blocks, key=lambda block: block.get("order") or 0)
        if first.get("category_type"):
            categories.add(str(first["category_type"]))
    return categories


def expected_denominators(payload: list[dict[str, Any]], subset: str) -> dict[str, Any]:
    text_pages = formula_pages = table_pages = reading_pages = 0
    for item in payload:
        raw_categories = raw_page_categories(item)
        matched_categories = matched_page_categories(item)
        has_text = bool(matched_categories & TEXT_CATEGORIES)
        has_formula = "equation_isolated" in raw_categories
        has_table = "table" in raw_categories
        has_reading_order = bool(
            matched_categories & (TEXT_CATEGORIES | {"equation_isolated", "table"})
        )
        text_pages += has_text
        formula_pages += has_formula
        table_pages += has_table
        reading_pages += has_reading_order
    return {
        "schema_version": 1,
        "subset": subset,
        "selected_pages": len(payload),
        "metrics": {
            "text_block": {"Edit_dist": text_pages},
            "display_formula": {"Edit_dist": formula_pages, "CDM": formula_pages},
            "table": {"TEDS": table_pages, "Edit_dist": table_pages},
            "reading_order": {"Edit_dist": reading_pages},
        },
    }


def main(args: argparse.Namespace) -> None:
    payload: list[dict[str, Any]] = json.loads(args.input.read_text(encoding="utf-8"))
    source_index: list[dict[str, Any]] = json.loads(args.index.read_text(encoding="utf-8"))
    if args.subset == "hard":
        payload = [
            item
            for item in payload
            if str(item["page_info"].get("page_attribute", {}).get("subset", "")).casefold()
            in HARD_SUBSETS
        ]
        source_index = [
            item
            for item in source_index
            if str(item["page_info"].get("page_attribute", {}).get("subset", "")).casefold()
            in HARD_SUBSETS
        ]
    expected = 296 if args.subset == "hard" else 1651
    if len(payload) != expected:
        raise RuntimeError(f"selected {len(payload)} evaluator pages, expected {expected}")
    ground_truth_names = [str(item["page_info"]["image_path"]) for item in payload]
    index_names = [str(item["page_info"]["image_path"]) for item in source_index]
    if len(ground_truth_names) != len(set(ground_truth_names)):
        raise RuntimeError("selected ground truth contains duplicate image_path values")
    if len(index_names) != len(set(index_names)):
        raise RuntimeError("source index contains duplicate image_path values")
    if set(ground_truth_names) != set(index_names):
        missing = sorted(set(index_names) - set(ground_truth_names))[:20]
        extra = sorted(set(ground_truth_names) - set(index_names))[:20]
        raise RuntimeError(
            f"evaluator slice does not match source index: missing={missing}, extra={extra}"
        )
    args.output.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
    if args.denominators is not None:
        args.denominators.parent.mkdir(parents=True, exist_ok=True)
        args.denominators.write_text(
            json.dumps(expected_denominators(payload, args.subset), indent=2) + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--subset", choices=("hard", "all"), required=True)
    parser.add_argument("--denominators", type=Path)
    main(parser.parse_args())
