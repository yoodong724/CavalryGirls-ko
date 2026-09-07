from __future__ import annotations

import copy
import json
from pathlib import Path
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from adapters.ui_maintenance import UiPatchError, expected_payload, patch_ui


ROOT = Path(__file__).resolve().parents[1]


class FakeObject:
    type = SimpleNamespace(name="MonoBehaviour")

    def __init__(self, record):
        self.path_id = record["path_id"]
        self.original = bytes.fromhex(record["source_hex"])
        self.data = None

    def get_raw_data(self):
        return self.original

    def set_raw_data(self, value):
        self.data = value


class UiMaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.recipe = json.loads((ROOT / "release/reference/ui-r04.json").read_text(encoding="utf-8"))

    def test_reported_chinese_defaults_are_replaced_in_both_variants(self):
        targets = {r["path_id"]: r for r in self.recipe["objects"]}
        for pid in (312174, 329595, 313244, 331992):
            source, result = expected_payload(targets[pid])
            native = pid in (312174, 329595)
            offset = 128 if native else 72
            length = struct.unpack_from("<i", result, offset)[0]
            text = result[offset + 4:offset + 4 + length].decode("utf-8")
            self.assertEqual(text, "복제 횟수: 3\n아이템 레벨: 2" if native else "업그레이드 및 개조 모델")
            self.assertNotIn(("复制次数" if native else "升级及改造型号").encode(), result)
            self.assertEqual(len(result) % 4, 0)
            old_length = struct.unpack_from("<i", source, offset)[0]
            self.assertEqual(source[offset + 4 + ((old_length + 3) & ~3):], result[offset + 4 + ((length + 3) & ~3):])
            if native:
                self.assertEqual(struct.unpack_from("<iq", result, 72), (0, 9108))
                self.assertEqual(source[:72], result[:72])
                self.assertEqual(source[84:128], result[84:128])
            else:
                self.assertEqual(source[:72], result[:72])

    def test_preflight_rejects_unknown_object_without_partial_writes(self):
        objects = {r["path_id"]: FakeObject(r) for r in self.recipe["objects"]}
        last = objects[self.recipe["objects"][-1]["path_id"]]
        last.original = last.original[:-1] + bytes([last.original[-1] ^ 1])
        environment = SimpleNamespace(file=SimpleNamespace(objects=objects))
        with patch("adapters.ui_maintenance.validate_font", return_value={}):
            with self.assertRaisesRegex(UiPatchError, "unknown UI object version"):
                patch_ui(environment, self.recipe)
        self.assertTrue(all(o.data is None for o in objects.values()))

    def test_only_detail_popup_spacing_changes_without_resizing_fonts(self):
        expected = {
            323048: (124, 1.0), 349023: (124, 1.0),
            337984: (124, 1.0), 312291: (124, 1.0),
            325914: (124, 1.0), 340986: (124, 1.0),
            338491: (52, 8.0), 313127: (52, 8.0),
            349586: (0x768, -5.0), 323591: (0x148, -5.0),
        }
        layout = {r["path_id"]: r for r in self.recipe["objects"] if r["kind"] == "layout"}
        self.assertEqual(set(layout), set(expected))
        for pid, (offset, value) in expected.items():
            before, after = expected_payload(layout[pid])
            self.assertEqual(struct.unpack_from("<f", after, offset)[0], value)
            self.assertEqual(len(before), len(after))
            self.assertEqual(before[:offset] + before[offset + 4:], after[:offset] + after[offset + 4:])
        # TargetSub's 20 is padding, not line spacing; it must remain untouched.
        self.assertTrue({338305, 313281}.isdisjoint(layout))

    def test_reapplication_is_idempotent_and_scope_is_exact(self):
        objects = {r["path_id"]: FakeObject(r) for r in self.recipe["objects"]}
        environment = SimpleNamespace(file=SimpleNamespace(objects=objects))
        with patch("adapters.ui_maintenance.validate_font", return_value={}):
            self.assertEqual(patch_ui(environment, self.recipe)["changed_path_ids"], sorted(objects))
            self.assertEqual(patch_ui(environment, self.recipe)["changed_path_ids"], [])
        for row in self.recipe["objects"]:
            self.assertEqual(objects[row["path_id"]].data, expected_payload(row)[1])

    def test_bad_recipe_hash_offset_and_overlapping_edits_are_rejected(self):
        row = self.recipe["objects"][0]
        bad = copy.deepcopy(row)
        bad["source_sha256"] = "0" * 64
        with self.assertRaises(UiPatchError):
            expected_payload(bad)
        bad = copy.deepcopy(row)
        bad["fields"][0]["offset"] += 4
        with self.assertRaises(UiPatchError):
            expected_payload(bad)
        bad = copy.deepcopy(row)
        bad["fields"].append(copy.deepcopy(bad["fields"][0]))
        with self.assertRaisesRegex(UiPatchError, "overlapping"):
            expected_payload(bad)

    def test_r05_compacts_the_active_body_without_changing_other_r04_repairs(self):
        recipe = json.loads((ROOT / "release/reference/ui-r05.json").read_text(encoding="utf-8"))
        self.assertEqual(recipe["translation_validation"], self.recipe["translation_validation"])
        old = {r["path_id"]: expected_payload(r)[1] for r in self.recipe["objects"]}
        new = {r["path_id"]: expected_payload(r)[1] for r in recipe["objects"]}
        self.assertEqual(set(new), set(old))
        self.assertEqual({pid for pid in old if old[pid] != new[pid]}, {349586, 323591})
        for pid, offset in ((349586, 0x768), (323591, 0x148)):
            self.assertEqual(old[pid][:offset] + old[pid][offset + 4:], new[pid][:offset] + new[pid][offset + 4:])
            spacing = struct.unpack_from("<f", new[pid], offset)[0]
            # Static calibration proxy from the r04 screenshot, not a render test.
            face_em = 30.4130859375 / 21
            observed_advance, observed_ink = 575 / 14, 22
            predicted_ratio = observed_advance * (face_em + spacing / 32) / (face_em - 5 / 32) / observed_ink
            self.assertGreaterEqual(predicted_ratio, 1.1)
            self.assertLessEqual(predicted_ratio, 1.15)
            r04_spacing = struct.unpack_from("<f", old[pid], offset)[0]
            old_ratio = observed_advance * (face_em + r04_spacing / 32) / (face_em - 5 / 32) / observed_ink
            self.assertGreater(old_ratio, 1.5)


if __name__ == "__main__":
    unittest.main()
