from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import unittest

from adapters.cavalry_girls import CellPatchError, patch_cells
from tools.maintenance.profile import (
    BuildProfile,
    ProfileError,
    validate_source_game,
)


class BuildProfileTests(unittest.TestCase):
    def test_source_validation_is_bound_to_profile_hashes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cg-profile-source-") as temp_name:
            root = Path(temp_name)
            paths = {
                "CavalryGirls_Data/resources.assets": b"resources",
                "CavalryGirls_Data/resources.assets.resS": b"resource-stream",
                "AssetBundles/fonts_assets_all.bundle": b"fonts",
            }
            for relative, data in paths.items():
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            profile = BuildProfile(
                path=root / "profile.json",
                version="3.0.2950",
                source_revision="f341ed52b0f2a51c24488de4f7b4bf32404c943244fe0263dde3f5d36a1f15ab",
                source_manifest=root / "source-hashes.json",
                source_manifest_sha256="0" * 64,
                source_hashes={relative: hashlib.sha256(data).hexdigest() for relative, data in paths.items()},
                text_target_ids=(8213,),
                record_count=1,
                title={"offset": 0, "size": 1},
                coverage={"qa": True, "l1": True, "l2": True, "blockers": []},
                artifacts={},
                raw={},
            )
            observed = validate_source_game(profile, root)
            self.assertEqual(observed, profile.source_hashes)
            (root / "AssetBundles/fonts_assets_all.bundle").write_bytes(b"changed")
            with self.assertRaises(ProfileError):
                validate_source_game(profile, root)

    def test_native_missing_trailing_cell_requires_explicit_profile(self) -> None:
        source = (
            b"Descriptions,Chinese,ChineseTraditional,English,Japanese\r\n"
            b"k,\xe4\xb8\xad\xe6\x96\x87,\xe7\xb9\x81,en\r\n"
            b"orphan,keep\r\n"
            b"tail\r\n"
        )
        profile = {
            "table_exceptions": [{
                "asset_path_id": 8213,
                "asset_sha256": hashlib.sha256(source).hexdigest(),
                "dialect": "native-raw-comma-newline-v1",
                "repaired_rows": [{
                    "row_index": 1,
                    "key": "k",
                    "raw_sha256": hashlib.sha256("k,中文,繁,en".encode()).hexdigest(),
                    "input_width": 4,
                    "target_column": "Japanese",
                }],
                "orphan_rows": [
                    {"row_index": 2, "raw_sha256": hashlib.sha256(b"orphan,keep").hexdigest(), "width": 2, "key": "orphan"},
                    {"row_index": 3, "raw_sha256": hashlib.sha256(b"tail").hexdigest(), "width": 1, "key": "tail"},
                ],
            }],
            "asset_path_ids": {"Descriptions": 8213},
        }
        edit = {
            "asset_path_id": 8213,
            "row_index": 1,
            "key": "k",
            "column": "Japanese",
            "target_cell_present": False,
            "expected_sha256": hashlib.sha256(b"").hexdigest(),
            "target_insertion": {
                "dialect": "native-raw-comma-newline-v1",
                "row_index": 1,
                "column": "Japanese",
                "original_width": 4,
                "output_width": 5,
                "insertion_offset": source.index(b"\r\n", source.index(b"\r\n") + 2),
                "raw_row_sha256": hashlib.sha256("k,中文,繁,en".encode()).hexdigest(),
            },
            "control_source_column": "Chinese",
            "control_source_sha256": hashlib.sha256("中文".encode()).hexdigest(),
            "translation": "한국어",
        }
        output, report = patch_cells(source, [edit], profile=profile)
        self.assertIn("en,한국어\r\n".encode(), output)
        self.assertTrue(output.endswith(b"orphan,keep\r\ntail\r\n"))
        self.assertEqual(report["profile_trailing_cell_count"], 1)
        self.assertEqual(report["input_sha256"], hashlib.sha256(source).hexdigest())
        self.assertFalse(report["byte_identical"])
        self.assertEqual(report["changed_cell_count"], 1)
        self.assertEqual(len(report["edits"]), 1)
        with self.assertRaises(CellPatchError):
            patch_cells(source, [edit])


if __name__ == "__main__":
    unittest.main()
