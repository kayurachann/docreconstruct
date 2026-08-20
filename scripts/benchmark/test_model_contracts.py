#!/usr/bin/env python3
"""Regression tests for model aliases and candidate install contracts."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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


class ProjectInstallTests(unittest.TestCase):
    def test_all_lanes_install_hybrid_and_smoke_the_cli(self) -> None:
        installer = Path(__file__).with_name("install_system.sh").read_text(encoding="utf-8")
        self.assertNotIn(".[pdf]", installer)
        self.assertEqual(installer.count(".[hybrid]"), 4)
        self.assertIn("python -m docreconstruct.cli --help >/dev/null", installer)

    def test_mineru_runtime_config_is_inventoried_at_every_boundary(self) -> None:
        workflow = (
            Path(__file__).parents[2] / ".github" / "workflows" / "source-benchmark.yml"
        ).read_text(encoding="utf-8")
        self.assertEqual(
            workflow.count(
                '--runtime-file "mineru-config=$MINERU_TOOLS_CONFIG_JSON"'
            ),
            3,
        )

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
