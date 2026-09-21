from __future__ import annotations

import copy
import hashlib
from pathlib import Path
import subprocess
import sys
import textwrap
import unittest

from adapters.cavalry_girls import CellPatchError, patch_cells


ROOT = Path(__file__).resolve().parents[1]
ASSET_PATH_IDS = {
    "Descriptions": 8213,
    "ConditionEvents": 8378,
    "SpecialMod": 8336,
    "Players_Japanese": 8219,
    "Players2_Japanese": 8254,
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class CliProfileImportTests(unittest.TestCase):
    def test_dynamic_adapter_load_works_without_project_root_on_sys_path(self):
        adapter = ROOT / "adapters/cavalry_girls.py"
        script = textwrap.dedent(
            f"""
            import hashlib
            import importlib.util
            from pathlib import Path
            import sys

            project_root = Path({str(ROOT)!r})
            assert str(project_root) not in sys.path
            adapter_path = Path({str(adapter)!r})
            spec = importlib.util.spec_from_file_location("cg_table_maintenance", adapter_path)
            module = importlib.util.module_from_spec(spec)
            sys.modules["cg_table_maintenance"] = module
            spec.loader.exec_module(module)

            source = (
                b"Descriptions,Chinese,ChineseTraditional,English,Japanese\\r\\n"
                + "k,中文,繁,en\\r\\n".encode()
            )
            row = "k,中文,繁,en".encode()
            profile = {{
                "dialect": {{
                    "asset_path_id": 8213,
                    "asset_sha256": hashlib.sha256(source).hexdigest(),
                    "dialect": "native-raw-comma-newline-v1",
                    "repaired_rows": [{{
                        "row_index": 1,
                        "key": "k",
                        "raw_sha256": hashlib.sha256(row).hexdigest(),
                        "input_width": 4,
                        "target_column": "Japanese",
                    }}],
                    "orphan_rows": [],
                }},
                "asset_path_ids": {{"Descriptions": 8213}},
            }}
            output, report = module.patch_cells(source, [], profile=profile)
            assert output == source
            assert report["input_sha256"] == hashlib.sha256(source).hexdigest()
            print("isolated dynamic profile import passed")
            """
        )
        completed = subprocess.run(
            [sys.executable, "-I", "-c", script],
            cwd=ROOT / "tools" / "maintenance",
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("isolated dynamic profile import passed", completed.stdout)

    def test_all_five_update_tables_use_explicit_path_ids_and_one_local_exception(self):
        descriptions = (
            b"Descriptions,Chinese,ChineseTraditional,English,Japanese\r\n"
            + "missing,中文,繁,en\r\n".encode()
        )
        raw_row = "missing,中文,繁,en".encode()
        profile = {
            "asset_path_ids": ASSET_PATH_IDS,
            "table_exceptions": [{
                "asset_path_id": ASSET_PATH_IDS["Descriptions"],
                "asset_sha256": sha(descriptions),
                "dialect": "native-raw-comma-newline-v1",
                "repaired_rows": [{
                    "row_index": 1,
                    "key": "missing",
                    "raw_sha256": sha(raw_row),
                    "input_width": 4,
                    "target_column": "Japanese",
                }],
                "orphan_rows": [],
            }],
        }
        insertion = descriptions.rindex(b"\r\n")
        missing_edit = {
            "asset_path_id": ASSET_PATH_IDS["Descriptions"],
            "row_index": 1,
            "key": "missing",
            "occurrence": 0,
            "column": "Japanese",
            "target_cell_present": False,
            "expected_sha256": sha(b""),
            "control_source_column": "Chinese",
            "control_source_sha256": sha("中文".encode()),
            "target_insertion": {
                "dialect": "native-raw-comma-newline-v1",
                "row_index": 1,
                "column": "Japanese",
                "inserted_value": "",
                "original_width": 4,
                "output_width": 5,
                "insertion_offset": insertion,
                "raw_row_sha256": sha(raw_row),
            },
            "translation": "한국어",
        }
        output, report = patch_cells(descriptions, [missing_edit], profile=profile)
        self.assertEqual(report["asset_path_id"], ASSET_PATH_IDS["Descriptions"])
        self.assertIn("en,한국어\r\n".encode(), output)

        headers = {
            "ConditionEvents": ("Chinese", "English", "Japanese", "ChineseTraditional", "ImagePath"),
            "SpecialMod": ("FileId", "Chinese", "ChineseTraditional", "English", "Japanese", "Comment"),
            "Players_Japanese": (
                "BattleRetreat", "Damage2", "Death", "Change", "ChangeSec", "ChangeLast",
                "ChangeSuccess", "VicBad", "VicNormal", "VicPerfect", "StartBad", "StartNormal",
                "StartPerfect", "GiftDis", "GiftNormal", "GiftLike", "GiftPerfect", "CommandRefuse",
                "Entrance", "Exit", "Refuse", "Meet", "Battle", "Promoting", "Touch", "Login",
            ),
            "Players2_Japanese": (
                "Shop", "ShopAi", "Restraunt", "RestrauntAi", "Beach", "BeachAi",
                "Onsen", "OnsenAi", "Cinema", "CinemaAi",
            ),
        }
        columns = {
            "ConditionEvents": "Japanese",
            "SpecialMod": "Japanese",
            "Players_Japanese": "BattleRetreat",
            "Players2_Japanese": "Shop",
        }
        for name, fields in headers.items():
            with self.subTest(name=name):
                row = ["key", *("source" for _ in fields)]
                source = (",".join((name, *fields)) + "\r\n" + ",".join(row) + "\r\n").encode()
                column_index = fields.index(columns[name]) + 1
                edit = {
                    "asset_path_id": ASSET_PATH_IDS[name],
                    "row_index": 1,
                    "key": "key",
                    "occurrence": 0,
                    "column": columns[name],
                    "expected_sha256": sha(b"source"),
                    "translation": "target",
                }
                patched, normal_report = patch_cells(source, [edit], profile=profile)
                self.assertEqual(normal_report["asset_path_id"], ASSET_PATH_IDS[name])
                self.assertNotEqual(patched, source)

    def test_profile_mapping_and_exception_identity_fail_closed(self):
        source = b"ConditionEvents,Chinese,English,Japanese,ChineseTraditional,ImagePath\r\nk,zh,en,ja,zht,img\r\n"
        profile = {"asset_path_ids": dict(ASSET_PATH_IDS), "table_exceptions": []}
        missing = copy.deepcopy(profile)
        del missing["asset_path_ids"]["ConditionEvents"]
        with self.assertRaisesRegex(CellPatchError, "missing ConditionEvents"):
            patch_cells(source, [], profile=missing)
        ambiguous = copy.deepcopy(profile)
        ambiguous["text_assets"] = [{"name": "ConditionEvents", "path_id": 9999}]
        with self.assertRaisesRegex(CellPatchError, "ambiguous"):
            patch_cells(source, [], profile=ambiguous)
        mismatched = copy.deepcopy(profile)
        mismatched["table_exceptions"] = [{
            "asset_name": "ConditionEvents",
            "asset_path_id": ASSET_PATH_IDS["Descriptions"],
            "dialect": "native-raw-comma-newline-v1",
        }]
        with self.assertRaisesRegex(CellPatchError, "path mismatches"):
            patch_cells(source, [], profile=mismatched)


if __name__ == "__main__":
    unittest.main()
