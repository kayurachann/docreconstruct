#!/usr/bin/env python3
"""Regression tests for model aliases and candidate install contracts."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import prepare_sources
from install_rapidocr_assets import install_assets
from inventory_environment import installation_contract
from inventory_environment import main as inventory_main
from materialize_pinned_models import (
    materialize_url_assets,
    verify_revision_aliases,
    write_revision_aliases,
)
from verify_install_contract import installation_requirements, verify


class RevisionAliasTests(unittest.TestCase):
    def snapshot(self) -> dict[str, object]:
        return {
            "repo": "demo/repo",
            "revision": "a" * 40,
            "revision_aliases": ["v2.3.0"],
        }

    @unittest.skipUnless(
        importlib.util.find_spec("huggingface_hub"),
        "huggingface_hub is installed only in candidate benchmark environments",
    )
    def test_declared_tag_resolves_from_offline_huggingface_cache(self) -> None:
        from huggingface_hub import snapshot_download

        with tempfile.TemporaryDirectory() as directory:
            hub = Path(directory)
            repo_root = hub / "models--demo--repo"
            snapshot = repo_root / "snapshots" / ("a" * 40)
            snapshot.mkdir(parents=True)
            (snapshot / "config.json").write_text("{}", encoding="utf-8")
            write_revision_aliases(repo_root, self.snapshot())

            with (
                patch.dict(os.environ, {"HF_HUB_OFFLINE": "1"}),
                patch.object(
                    socket.socket,
                    "connect",
                    side_effect=AssertionError("offline alias resolution attempted network access"),
                ),
            ):
                resolved = snapshot_download(
                    repo_id="demo/repo",
                    revision="v2.3.0",
                    cache_dir=hub,
                    local_files_only=True,
                )

            self.assertEqual(Path(resolved), snapshot)
            verify_revision_aliases(repo_root, self.snapshot())

    def test_undeclared_ref_is_rejected_instead_of_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo_root = Path(directory)
            refs = repo_root / "refs"
            refs.mkdir()
            (refs / "main").write_text("b" * 40, encoding="ascii")
            with self.assertRaisesRegex(RuntimeError, "undeclared revision aliases"):
                write_revision_aliases(repo_root, self.snapshot())

    def test_declared_ref_with_wrong_target_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo_root = Path(directory)
            refs = repo_root / "refs"
            refs.mkdir()
            (refs / "v2.3.0").write_text("b" * 40, encoding="ascii")
            with self.assertRaisesRegex(RuntimeError, "refusing to replace revision alias"):
                write_revision_aliases(repo_root, self.snapshot())

    def test_docling_tag_contract_does_not_alias_different_main(self) -> None:
        pins = json.loads(Path(__file__).with_name("model-pins.json").read_text(encoding="utf-8"))
        snapshots = pins["systems"]["docling"]["huggingface_snapshots"]
        models = next(
            snapshot
            for snapshot in snapshots
            if snapshot["repo"] == "docling-project/docling-models"
        )
        self.assertEqual(models["revision_aliases"], ["v2.3.0"])
        self.assertNotIn("main", models["revision_aliases"])


class InstallContractTests(unittest.TestCase):
    def mineru_contract(self) -> dict[str, object]:
        return {
            "package": "mineru==3.4.5",
            "install_spec": "mineru[pipeline]==3.4.5",
            "compatibility_packages": ["six==1.17.0"],
            "required_imports": ["mineru", "torch"],
        }

    def test_mineru_pipeline_extra_and_six_are_exact(self) -> None:
        self.assertEqual(
            installation_requirements(self.mineru_contract()),
            ["mineru[pipeline]==3.4.5", "six==1.17.0"],
        )

    def test_verification_imports_torch(self) -> None:
        versions = {"mineru": "3.4.5", "six": "1.17.0"}
        with (
            patch("importlib.metadata.version", side_effect=versions.__getitem__),
            patch("importlib.import_module") as importer,
        ):
            verify(self.mineru_contract())
        self.assertEqual(
            [call.args[0] for call in importer.call_args_list],
            ["mineru", "torch"],
        )

    def test_missing_torch_fails_the_install_smoke(self) -> None:
        versions = {"mineru": "3.4.5", "six": "1.17.0"}

        def import_module(name: str) -> object:
            if name == "torch":
                raise ModuleNotFoundError("No module named 'torch'")
            return object()

        with (
            patch("importlib.metadata.version", side_effect=versions.__getitem__),
            patch("importlib.import_module", side_effect=import_module),
            self.assertRaisesRegex(ModuleNotFoundError, "torch"),
        ):
            verify(self.mineru_contract())

    def test_public_inventory_records_install_spec_and_pin_digest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pins = Path(directory) / "pins.json"
            pins.write_text(
                json.dumps({"systems": {"mineru": self.mineru_contract()}}),
                encoding="utf-8",
            )
            record = installation_contract(pins, "mineru")
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record["install_spec"], "mineru[pipeline]==3.4.5")
        self.assertEqual(record["compatibility_packages"], ["six==1.17.0"])
        self.assertEqual(record["required_imports"], ["mineru", "torch"])
        self.assertRegex(str(record["model_pins_sha256"]), r"^[0-9a-f]{64}$")


class RapidOcrAssetTests(unittest.TestCase):
    def record(self, data: bytes = b"pinned-model") -> dict[str, object]:
        return {
            "path": "model.pth",
            "url": "https://example.invalid/model.pth",
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }

    def test_complete_cache_is_verified_offline_without_network(self) -> None:
        data = b"pinned-model"
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            (cache / "model.pth").write_bytes(data)
            with patch(
                "urllib.request.urlopen",
                side_effect=AssertionError("offline materialization attempted network access"),
            ):
                materialize_url_assets(cache, [self.record(data)], offline=True)

    def test_missing_asset_fails_offline_without_network(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch("urllib.request.urlopen") as network,
                self.assertRaisesRegex(RuntimeError, "missing offline"),
            ):
                materialize_url_assets(Path(directory), [self.record()], offline=True)
            network.assert_not_called()

    def test_wrong_asset_bytes_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            (cache / "model.pth").write_bytes(b"wrong")
            with self.assertRaisesRegex(RuntimeError, "pinned cache file changed"):
                materialize_url_assets(cache, [self.record()], offline=True)

    def test_install_copy_and_post_copy_verification(self) -> None:
        data = b"pinned-model"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / "cache"
            models = root / "site-packages" / "rapidocr" / "models"
            cache.mkdir()
            (cache / "model.pth").write_bytes(data)
            records = [self.record(data)]
            install_assets(cache, models, records, verify_only=False)
            install_assets(cache, models, records, verify_only=True)
            (models / "model.pth").write_bytes(b"mutated")
            with self.assertRaisesRegex(RuntimeError, "pinned cache file changed"):
                install_assets(cache, models, records, verify_only=True)

    def test_docling_contract_matches_cold_smoke_assets(self) -> None:
        pins = json.loads(Path(__file__).with_name("model-pins.json").read_text(encoding="utf-8"))
        system = pins["systems"]["docling"]
        self.assertEqual(system["compatibility_packages"], ["rapidocr==3.9.2"])
        self.assertEqual(
            installation_requirements(system),
            ["docling==2.120.3", "rapidocr==3.9.2"],
        )
        assets = {
            record["path"]: (record["bytes"], record["sha256"])
            for record in system["rapidocr_assets"]
        }
        self.assertEqual(
            assets,
            {
                "PP-OCRv6_det_small.pth": (
                    10248727,
                    "fbdc74c97ea7b770ab22cbdc1bba01a52bdf1975efcf3442057356d622b05d54",
                ),
                "ch_ptocr_mobile_v2.0_cls_mobile.pth": (
                    588638,
                    "bfe13860824b3365c0c7f7ccfcddc8ff11645c60051739ff18bc9913f60c98e1",
                ),
                "PP-OCRv6_rec_small.pth": (
                    21326017,
                    "0107b2ad694ccc9b1db7cf9ed3ffbc93d1795d9e08d9cf823127243a87bce516",
                ),
                "ppocrv6_dict.txt": (
                    74947,
                    "b5f2bfe2bdd9448429e3e82b51c789775d9b42f2403d082b00662eb77e401c5d",
                ),
            },
        )
        self.assertEqual(
            {record["url"] for record in system["rapidocr_assets"]},
            {
                "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.9.2/torch/PP-OCRv6/det/PP-OCRv6_det_small.pth",
                "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.9.2/torch/PP-OCRv4/cls/ch_ptocr_mobile_v2.0_cls_mobile.pth",
                "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.9.2/torch/PP-OCRv6/rec/PP-OCRv6_rec_small.pth",
                "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.9.2/paddle/PP-OCRv6/rec/PP-OCRv6_rec_small/ppocrv6_dict.txt",
            },
        )


class TesseractAssetTests(unittest.TestCase):
    def test_tsv_config_is_committed_and_not_downloaded(self) -> None:
        benchmark = Path(__file__).parent
        asset = benchmark / "assets" / "tessdata-config-tsv"
        self.assertEqual(asset.read_bytes(), b"tessedit_create_tsv 1\n")
        self.assertEqual(
            hashlib.sha256(asset.read_bytes()).hexdigest(),
            "59d079bb75d8b3d7c839a3564580cb559e362c93a9d70f234e421c0c3e767e04",
        )
        installer = (benchmark / "install_system.sh").read_text(encoding="utf-8")
        self.assertNotIn("$base/configs/tsv", installer)
        self.assertIn(
            'install -m 0644 scripts/benchmark/assets/tessdata-config-tsv "$tess_root/configs/tsv"',
            installer,
        )


class DirectSourceMaterializationTests(unittest.TestCase):
    def item(self, payload: bytes = b"raw-image") -> dict[str, object]:
        return {
            "page_info": {
                "image_path": "nested/page.png",
                "page_attribute": {"subset": "layout_hard"},
            },
            "source_bytes": len(payload),
            "source_sha256": hashlib.sha256(payload).hexdigest(),
        }

    def test_source_paths_are_relative_images_from_the_committed_allowlist(self) -> None:
        self.assertEqual(
            prepare_sources.source_relative_path("nested/page.png").as_posix(),
            "nested/page.png",
        )
        for unsafe in (
            "../page.png",
            "/page.png",
            "C:/page.png",
            "nested\\page.png",
            "page\n.png",
            "page.pdf",
        ):
            with self.subTest(unsafe=unsafe), self.assertRaises(ValueError):
                prepare_sources.source_relative_path(unsafe)

    def test_only_https_hugging_face_redirect_hosts_are_allowed(self) -> None:
        prepare_sources.validate_download_url("https://huggingface.co/datasets/demo")
        prepare_sources.validate_download_url("https://cas-bridge.xethub.hf.co/signed-model-asset")
        for unsafe in (
            "http://huggingface.co/datasets/demo",
            "https://huggingface.co.evil.invalid/datasets/demo",
            "https://user@huggingface.co/datasets/demo",
            "https://huggingface.co:444/datasets/demo",
        ):
            with self.subTest(unsafe=unsafe), self.assertRaises(RuntimeError):
                prepare_sources.validate_download_url(unsafe)

    def test_declared_source_size_has_a_hard_upper_bound(self) -> None:
        item = self.item()
        item["source_bytes"] = 64 * 1024 * 1024 + 1
        with self.assertRaisesRegex(ValueError, "between 1"):
            prepare_sources.validate_source_index_item(item)

    def test_oversized_response_is_atomic_and_leaves_no_temporary_file(self) -> None:
        class Response:
            headers: dict[str, str] = {}

            def __enter__(self) -> Response:
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def geturl(self) -> str:
                return "https://cdn-lfs.hf.co/pinned-source"

            def read(self, _size: int) -> bytes:
                return b"abc"

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            item = self.item(b"ok")
            with (
                patch("prepare_sources.urllib.request.urlopen", return_value=Response()),
                self.assertRaisesRegex(RuntimeError, "exceeds 2 bytes"),
            ):
                prepare_sources._download_source_once(item, root)
            self.assertFalse((root / "nested" / "page.png").exists())
            self.assertEqual(list(root.rglob(".source-*")), [])

    def test_transient_download_failure_retries_without_relaxing_verification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "nested" / "page.png"
            with (
                patch(
                    "prepare_sources._download_source_once",
                    side_effect=[OSError("transient"), destination],
                ) as attempt,
                patch("prepare_sources.time.sleep") as backoff,
            ):
                result = prepare_sources.download_source(self.item(), Path(directory))
        self.assertEqual(result, destination)
        self.assertEqual(attempt.call_count, 2)
        backoff.assert_called_once_with(1)


class EvictionProofWorkflowTests(unittest.TestCase):
    WORKFLOW_ROOT = Path(__file__).parents[2] / ".github" / "workflows"

    @classmethod
    def workflow(cls, name: str) -> str:
        return (cls.WORKFLOW_ROOT / name).read_text(encoding="utf-8")

    @classmethod
    def main(cls) -> str:
        return cls.workflow("source-benchmark.yml")

    @classmethod
    def inference(cls) -> str:
        return cls.workflow("source-inference-shard.yml")

    @classmethod
    def preparation(cls) -> str:
        return cls.workflow("source-prepare-model.yml")

    @staticmethod
    def job(workflow: str, name: str) -> str:
        tail = workflow.split(f"\n  {name}:\n", maxsplit=1)[1]
        return re.split(r"\n  (?=[a-z0-9-]+:\n)", tail, maxsplit=1)[0]

    def test_reusable_workers_are_call_only_and_pin_every_action(self) -> None:
        for name in ("source-inference-shard.yml", "source-prepare-model.yml"):
            workflow = self.workflow(name)
            with self.subTest(name=name):
                self.assertIn("on:\n  workflow_call:\n", workflow)
                self.assertNotIn("workflow_dispatch", workflow)
                revisions = re.findall(r"uses: actions/[^@\s]+@([^\s]+)", workflow)
                self.assertGreater(len(revisions), 0)
                self.assertTrue(
                    all(re.fullmatch(r"[0-9a-f]{40}", revision) for revision in revisions)
                )

    def test_source_handoff_is_direct_exact_and_never_cached_or_uploaded(self) -> None:
        inference = self.inference()
        combined = self.main() + self.preparation() + inference
        source_step = inference.split(
            "- name: Download and byte-verify this GT-free raw source shard", maxsplit=1
        )[1].split("\n      - name:", maxsplit=1)[0]
        self.assertNotIn("if:", source_step)
        self.assertIn("python scripts/benchmark/prepare_sources.py", source_step)
        self.assertIn('--shard-index "$SHARD_INDEX"', source_step)
        self.assertIn('--shard-count "$SHARD_COUNT"', source_step)
        self.assertNotIn("--verify-only", source_step)
        self.assertNotIn("source-benchmark-input-", combined)
        for upload in (
            block
            for block in (self.preparation() + inference).split("\n      - name:")
            if "actions/upload-artifact@" in block
        ):
            self.assertNotIn("/corpus", upload)
            self.assertNotIn("prewarm-corpus", upload)

    def test_hard_uses_four_shards_and_full_uses_exactly_twenty(self) -> None:
        main = self.main()
        hard = "'[0,1,2,3]'"
        full = "'[0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19]'"
        self.assertEqual(main.count(hard), 4)
        self.assertEqual(main.count(full), 4)
        self.assertIn("SHARD_COUNT: ${{ inputs.suite == 'hard' && '4' || '20' }}", main)
        self.assertEqual(main.count("max-parallel: 4"), 4)

    def test_benchmark_runs_cannot_contend_across_hard_and_full(self) -> None:
        main = self.main()
        concurrency = main.split("concurrency:\n", maxsplit=1)[1].split("\nenv:\n", maxsplit=1)[0]
        self.assertIn("group: source-benchmark-${{ github.ref }}", concurrency)
        self.assertNotIn("inputs.suite", concurrency)
        self.assertIn("cancel-in-progress: false", concurrency)

    def test_model_lanes_prepare_and_consume_sequentially_even_after_failure(self) -> None:
        main = self.main()
        docling = self.job(main, "inference-docling")
        prepare_mineru = self.job(main, "prepare-mineru")
        mineru = self.job(main, "inference-mineru")
        prepare_marker = self.job(main, "prepare-marker")
        marker = self.job(main, "inference-marker")
        self.assertIn("needs: prepare-docling", docling)
        self.assertIn("needs: inference-docling", prepare_mineru)
        self.assertIn("needs: prepare-mineru", mineru)
        self.assertIn("needs: inference-mineru", prepare_marker)
        self.assertIn("needs: prepare-marker", marker)
        for job in (docling, prepare_mineru, mineru, prepare_marker, marker):
            self.assertIn("if: ${{ always() }}", job)
        self.assertNotIn("source-benchmark-model-", main)
        self.assertNotIn("source-benchmark-input-", main)

    def test_model_key_contract_matches_preparation_and_inference(self) -> None:
        preparation = self.preparation()
        inference = self.inference()
        prepare_key = next(
            line.strip() for line in preparation.splitlines() if line.strip().startswith("key:")
        )
        inference_key = next(
            line.strip() for line in inference.splitlines() if line.strip().startswith("key:")
        )
        self.assertEqual(prepare_key, inference_key)
        for contract in (
            "${{ inputs.system }}",
            "model-pins.json",
            "install_system.sh",
            "verify_install_contract.py",
            "prepare_sources.py",
            "source-benchmark.yml",
            "source-prepare-model.yml",
            "source-inference-shard.yml",
        ):
            self.assertIn(contract, prepare_key)
        self.assertIn("uses: actions/cache@", preparation)
        self.assertIn("uses: actions/cache/restore@", inference)
        self.assertNotIn("actions/cache@", inference)

    def test_model_inventory_is_checked_before_and_after_measurement(self) -> None:
        inference = self.inference()
        ordered = [
            "Restore only the currently active lane's model cache",
            "Download the active lane's small prepared inventory",
            "Reject an incomplete restored model cache",
            "Inventory the restored model immediately before measurement",
            "Forbid remote model mutation during measured inference",
            "Run cold-isolated source inference",
            "Inventory the realized runtime and model blobs after measurement",
            "Prove measured inference did not mutate model bytes",
        ]
        positions = [inference.index(label) for label in ordered]
        self.assertEqual(positions, sorted(positions))
        self.assertIn(
            '--prepared "$RUNNER_TEMP/prepared-model-inventory/model-cache-manifest.json"',
            inference,
        )
        self.assertIn('--inference "$RUNNER_TEMP/model-cache-before-inference.json"', inference)
        self.assertIn('--prepared "$RUNNER_TEMP/model-cache-before-inference.json"', inference)
        self.assertIn('--inference "$RUNNER_TEMP/environment.json"', inference)
        self.assertIn('echo "HF_HUB_OFFLINE=1"', inference)

    def test_uploads_exclude_raw_and_model_bytes(self) -> None:
        preparation = self.preparation()
        inference = self.inference()
        prepare_upload = next(
            block
            for block in preparation.split("\n      - name:")
            if "actions/upload-artifact@" in block
        )
        self.assertIn("path: ${{ runner.temp }}/model-cache-manifest.json", prepare_upload)
        self.assertNotIn(".benchmark-cache", prepare_upload)
        inference_uploads = [
            block
            for block in inference.split("\n      - name:")
            if "actions/upload-artifact@" in block
        ]
        self.assertEqual(len(inference_uploads), 2)
        allowed_paths = {
            "path: ${{ runner.temp }}/public-artifact",
            "path: ${{ runner.temp }}/infrastructure-failure",
        }
        self.assertEqual(
            {
                next(
                    line.strip() for line in block.splitlines() if line.strip().startswith("path:")
                )
                for block in inference_uploads
            },
            allowed_paths,
        )
        for upload in inference_uploads:
            self.assertNotIn(".benchmark-cache", upload)
            self.assertNotIn("/corpus", upload)
            self.assertNotIn("model-cache-before-inference", upload)

    def test_setup_failure_is_publicly_classified_as_infrastructure_invalid(self) -> None:
        inference = self.inference()
        main = self.main()
        self.assertIn('"classification": "infrastructure-invalid"', inference)
        self.assertIn("no candidate quality score", inference)
        official = self.job(main, "official-evaluation")
        for dependency in (
            "inference-tesseract",
            "inference-docling",
            "inference-mineru",
            "inference-marker",
            "prepare-evaluator-runtime",
        ):
            self.assertIn(f"- {dependency}", official)
        self.assertIn("if: ${{ always() }}", official)
        self.assertIn("python scripts/benchmark/validate_predictions.py", official)
        self.assertIn('--shard-count "$SHARD_COUNT"', official)
        self.assertIn("- name: Stage aggregate evaluator evidence", official)

    def test_evaluator_cache_cannot_overlap_model_handoff(self) -> None:
        evaluator = self.job(self.main(), "prepare-evaluator-runtime")
        self.assertIn(
            "needs: [inference-tesseract, inference-docling, inference-mineru, inference-marker]",
            evaluator,
        )
        self.assertIn("if: ${{ always() }}", evaluator)

    def test_workflows_do_not_request_paid_cache_or_write_permissions(self) -> None:
        combined = self.main() + self.preparation() + self.inference()
        self.assertNotIn("actions: write", combined)
        self.assertNotIn("cache size", combined.casefold())
        self.assertNotIn("api.github.com", combined)


class ProjectInstallTests(unittest.TestCase):
    def test_all_lanes_install_hybrid_and_smoke_the_cli(self) -> None:
        installer = Path(__file__).with_name("install_system.sh").read_text(encoding="utf-8")
        self.assertNotIn(".[pdf]", installer)
        self.assertEqual(installer.count(".[hybrid]"), 4)
        self.assertIn("python -m docreconstruct.cli --help >/dev/null", installer)

    def test_mineru_runtime_config_is_inventoried_at_every_boundary(self) -> None:
        workflows = Path(__file__).parents[2] / ".github" / "workflows"
        preparation = (workflows / "source-prepare-model.yml").read_text(encoding="utf-8")
        inference = (workflows / "source-inference-shard.yml").read_text(encoding="utf-8")
        needle = '--runtime-file "mineru-config=$MINERU_TOOLS_CONFIG_JSON"'
        self.assertEqual(preparation.count(needle), 2)
        self.assertEqual(inference.count(needle), 2)

    def inventory_args(self, root: Path, report: Path) -> argparse.Namespace:
        return argparse.Namespace(
            output=root / "environment.json",
            pip_report=report,
            model_pins=None,
            system=None,
            cache_root=[],
            runtime_command=[],
            runtime_file=[],
        )

    def write_project_report(self, path: Path, extra: str) -> None:
        path.write_text(
            json.dumps(
                {
                    "install": [
                        {
                            "metadata": {"name": "docreconstruct", "version": "0.1.0"},
                            "download_info": {"dir_info": {"editable": True}},
                            "requested": True,
                            "requested_extras": [extra],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

    def test_pip_inventory_preserves_hybrid_request(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = root / "pip.json"
            self.write_project_report(report, "hybrid")
            inventory_main(self.inventory_args(root, report))
            payload = json.loads((root / "environment.json").read_text(encoding="utf-8"))
        project = next(
            item for item in payload["pip_install_inventory"] if item["name"] == "docreconstruct"
        )
        self.assertEqual(project["requested_extras"], ["hybrid"])

    def test_pdf_only_project_report_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = root / "pip.json"
            self.write_project_report(report, "pdf")
            with self.assertRaisesRegex(RuntimeError, "exactly the hybrid extra"):
                inventory_main(self.inventory_args(root, report))


if __name__ == "__main__":
    unittest.main()
