from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from adapters.ui_maintenance import UiPatchError, _deferred_width_result, expected_payload, patch_ui


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


class FakeRectTransform:
    type = SimpleNamespace(name="RectTransform")

    def __init__(self, raw, tree):
        self.raw = raw
        self.tree = tree

    def get_raw_data(self):
        return self.raw

    def read_typetree(self):
        return self.tree


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

    def test_r16_deferred_width_accepts_only_pinned_source_and_full_stretch_layout(self):
        recipe = {
            "patch_revision": "r16",
            "source_revision": "2528f66eaaaf86791762cc18980a30851ed7a5e6dea448167fdeb7898dd60a1e",
            "source_asset": {"resources_assets_sha256": "48566a74e7ad52b2ab03f30363dfc61a29fa3fa7ca13578b423c3151b0f102ad"},
            "release_scope": "stage8_local_candidate",
            "runtime_verified": False,
        }
        record = {
            "path_id": 709394, "source_sha256": "a" * 64,
            "width_check": {
                "mode": "deferred_dynamic_layout", "reason": "dynamic layout",
                "debt_id": "ui700767_dynamic_width", "nominal_required_width": 256.0,
                "evidence": {
                    "text_source_sha256": "a" * 64,
                    "rect_transform_path_id": 1, "rect_transform_sha256": "",
                    "parent_rect_transform_path_id": 2, "parent_rect_transform_sha256": "",
                    "anchor_min": [0.0, 0.0], "anchor_max": [1.0, 1.0], "size_delta": [0.0, 0.0],
                },
            },
        }
        evidence = record["width_check"]["evidence"]
        rect_raw, parent_raw = b"r16-rect", b"r16-parent"
        evidence["rect_transform_sha256"] = hashlib.sha256(rect_raw).hexdigest()
        evidence["parent_rect_transform_sha256"] = hashlib.sha256(parent_raw).hexdigest()
        tree = {
            "m_Father": {"m_FileID": 0, "m_PathID": evidence["parent_rect_transform_path_id"]},
            "m_AnchorMin": {"x": 0.0, "y": 0.0},
            "m_AnchorMax": {"x": 1.0, "y": 1.0},
            "m_SizeDelta": {"x": 0.0, "y": 0.0},
        }
        environment = SimpleNamespace(file=SimpleNamespace(objects={
            evidence["rect_transform_path_id"]: FakeRectTransform(rect_raw, tree),
            evidence["parent_rect_transform_path_id"]: FakeRectTransform(parent_raw, {}),
        }))
        measured = [record["width_check"]["nominal_required_width"]]

        result = _deferred_width_result(environment, recipe, record, measured)
        self.assertEqual(result["width_status"], "unverified")
        self.assertFalse(result["runtime_verified"])
        self.assertEqual(result["due_gate"], "public_release")
        self.assertEqual(result["debt_id"], "ui700767_dynamic_width")

        for mutation in (
            "source", "asset", "path", "debt", "text", "rect_hash", "parent_hash",
            "parent", "anchor", "nominal", "scope", "runtime",
        ):
            bad_recipe, bad_record, bad_environment = copy.deepcopy((recipe, record, environment))
            bad_evidence = bad_record["width_check"]["evidence"]
            if mutation == "source":
                bad_recipe["source_revision"] = "0" * 64
            elif mutation == "asset":
                bad_recipe["source_asset"]["resources_assets_sha256"] = "0" * 64
            elif mutation == "path":
                bad_record["path_id"] = 708703
            elif mutation == "debt":
                bad_record["width_check"]["debt_id"] = "unknown"
            elif mutation == "text":
                bad_evidence["text_source_sha256"] = "0" * 64
            elif mutation == "rect_hash":
                bad_evidence["rect_transform_sha256"] = "0" * 64
            elif mutation == "parent_hash":
                bad_evidence["parent_rect_transform_sha256"] = "0" * 64
            elif mutation == "parent":
                bad_environment.file.objects[bad_evidence["rect_transform_path_id"]].tree["m_Father"]["m_PathID"] = 1
            elif mutation == "anchor":
                bad_evidence["anchor_max"] = [0.0, 0.0]
            elif mutation == "nominal":
                bad_record["width_check"]["nominal_required_width"] += 1
            elif mutation == "scope":
                bad_recipe["release_scope"] = "public_release"
            else:
                bad_recipe["runtime_verified"] = True
            with self.subTest(mutation=mutation), self.assertRaises(UiPatchError):
                _deferred_width_result(bad_environment, bad_recipe, bad_record, measured)

    def test_r17_deferred_width_requires_pinned_source_and_parent_layout(self):
        recipe = {
            "patch_revision": "r17",
            "source_revision": "3e5263667aff29f5e09657d118e3481436fa6d75bae8bcef9b97d73e5558ec23",
            "source_asset": {"resources_assets_sha256": "2a45809d07d02f09f9e279eca46e80aa4cf06028018da262be6305489e42f05e"},
            "release_scope": "stage8_local_candidate",
            "runtime_verified": False,
        }
        record = {
            "path_id": 709394,
            "source_sha256": "e0868b8fd18cff1b52307564ae9f6df70d74ce035031e14fed4ee557e4073426",
            "width_check": {
                "mode": "deferred_dynamic_layout", "reason": "parent-controlled width",
                "debt_id": "ui700767_dynamic_width", "nominal_required_width": 256.6611328125,
                "evidence": {
                    "text_source_sha256": "e0868b8fd18cff1b52307564ae9f6df70d74ce035031e14fed4ee557e4073426",
                    "rect_transform_path_id": 660801, "rect_transform_sha256": hashlib.sha256(b"r17-rect").hexdigest(),
                    "parent_rect_transform_path_id": 666632,
                    "parent_rect_transform_sha256": hashlib.sha256(b"r17-parent").hexdigest(),
                    "anchor_min": [0.0, 0.0], "anchor_max": [1.0, 1.0], "size_delta": [0.0, 0.0],
                },
            },
        }
        tree = {
            "m_Father": {"m_FileID": 0, "m_PathID": 666632},
            "m_AnchorMin": {"x": 0.0, "y": 0.0},
            "m_AnchorMax": {"x": 1.0, "y": 1.0},
            "m_SizeDelta": {"x": 0.0, "y": 0.0},
        }
        environment = SimpleNamespace(file=SimpleNamespace(objects={
            660801: FakeRectTransform(b"r17-rect", tree),
            666632: FakeRectTransform(b"r17-parent", {}),
        }))
        measured = [256.6611328125]

        result = _deferred_width_result(environment, recipe, record, measured)
        self.assertEqual((result["width_status"], result["runtime_verified"], result["due_gate"]),
                         ("unverified", False, "public_release"))
        for mutation in ("source", "asset", "path", "parent_hash", "parent_pointer", "layout"):
            bad_recipe, bad_record, bad_environment = copy.deepcopy((recipe, record, environment))
            evidence = bad_record["width_check"]["evidence"]
            if mutation == "source":
                bad_recipe["source_revision"] = "0" * 64
            elif mutation == "asset":
                bad_recipe["source_asset"]["resources_assets_sha256"] = "0" * 64
            elif mutation == "path":
                bad_record["path_id"] = 708703
            elif mutation == "parent_hash":
                evidence["parent_rect_transform_sha256"] = "0" * 64
            elif mutation == "parent_pointer":
                bad_environment.file.objects[660801].tree["m_Father"]["m_PathID"] = 660801
            else:
                bad_environment.file.objects[660801].tree["m_AnchorMax"]["x"] = 0.0
            with self.subTest(mutation=mutation), self.assertRaises(UiPatchError):
                _deferred_width_result(bad_environment, bad_recipe, bad_record, measured)


if __name__ == "__main__":
    unittest.main()
