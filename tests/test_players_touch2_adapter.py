"""Portable current Players_Japanese layout and Touch2 regression."""
import hashlib
import json
from pathlib import Path
import unittest

from adapters import cavalry_girls as cg

ROOT = Path(__file__).resolve().parents[1]
MAP = ROOT / "localization/corpus/build26175764/patch-map.jsonl"
ASSET_SHA = "7bb7af86171728932f104597bdaf06b03354b34cde951709f05a15504d5ad66e"
HEADER = (
    "Players_Japanese,BattleRetreat,Damage2,Death,Change,ChangeSec,ChangeLast,"
    "ChangeSuccess,VicBad,VicNormal,VicPerfect,StartBad,StartNormal,StartPerfect,"
    "GiftDis,GiftNormal,GiftLike,GiftPerfect,CommandRefuse,Entrance,Exit,Refuse,"
    "Meet,Battle,Promoting,Touch,Touch2,Login"
).split(",")


def reconstructed_players():
    """Rebuild native bytes solely from public cell evidence and independent header."""
    rows = {}
    for line in MAP.read_text(encoding="utf-8").splitlines():
        item = json.loads(line)
        locator = item["target_locator"]
        if locator["asset_path_id"] != 8337:
            continue
        index, key, column = item["target_row_index"], locator["key"], locator["column"]
        if index not in range(1, 6) or key != str(index - 1) or locator["occurrence"] != 0:
            raise AssertionError("Players row identity changed")
        if column not in HEADER[1:] or column in rows.setdefault(index, {}):
            raise AssertionError("Players column identity changed or duplicated")
        value = item["target_text"]
        if hashlib.sha256(value.encode("utf-8")).hexdigest() != item["target_text_sha256"]:
            raise AssertionError("Players source cell hash changed")
        rows[index][column] = value
    if set(rows) != set(range(1, 6)) or any(set(row) != set(HEADER[1:]) for row in rows.values()):
        raise AssertionError("Players cell coverage incomplete")
    lines = [",".join(HEADER)]
    lines += [",".join([str(index - 1)] + [rows[index][column] for column in HEADER[1:]])
              for index in range(1, 6)]
    data = ("\r\n".join(lines) + "\r\n").encode("utf-8")
    if hashlib.sha256(data).hexdigest() != ASSET_SHA:
        raise AssertionError("reconstructed Players asset hash changed")
    return data


class PlayersTouch2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = reconstructed_players()
        cls.asset = {"name": "Players_Japanese", "path_id": 8337,
                     "catalog_paths": ["Text/Table/Players_Japanese"], "sha256": ASSET_SHA}

    def profile_for_current_asset(self):
        self.assertTrue(hasattr(cg, "players_touch2_profile"), "current layout profile support is missing")
        return {"asset_path_ids": {self.asset["name"]: self.asset["path_id"]},
                "table_layouts": [cg.players_touch2_profile(self.asset)]}

    def test_reconstructed_asset_has_exact_native_layout(self):
        self.assertEqual(hashlib.sha256(self.data).hexdigest(), ASSET_SHA)
        self.assertEqual(len(self.data), 12397)
        self.assertEqual(self.data.count(b"\r\n"), 6)
        self.assertEqual(len(HEADER), 28)
        self.assertEqual(self.data.split(b"\r\n")[0].decode().split(","), HEADER)

    def test_current_fixture_touch2_and_login_are_independent_cells(self):
        row = self.data.split(b"\r\n")[1].split(b",")
        edits = [{"asset_path_id": 8337, "row_index": 1, "key": "0", "occurrence": 0,
                  "column": name, "expected_sha256": hashlib.sha256(row[index]).hexdigest(),
                  "translation": value}
                 for name, index, value in (("Touch2", 26, "새 터치 대사|다른 터치 대사"),
                                            ("Login", 27, "새 로그인 대사"))]
        output, report = cg.patch_cells(self.data, edits, profile=self.profile_for_current_asset())
        result = output.split(b"\r\n")[1].split(b",")
        self.assertEqual(len(result), 28)
        self.assertEqual(result[25], row[25])
        self.assertEqual(result[26:28], ["새 터치 대사|다른 터치 대사".encode(), "새 로그인 대사".encode()])
        self.assertEqual(report["changed_cell_count"], 2)
        self.assertEqual(report["raw_field_count"], 28)

    def test_original_asset_noop_is_idempotent(self):
        row = self.data.split(b"\r\n")[1].split(b",")
        edit = {"asset_path_id": 8337, "row_index": 1, "key": "0", "occurrence": 0,
                "column": "Touch2", "expected_sha256": hashlib.sha256(row[26]).hexdigest(),
                "translation": row[26].decode()}
        first, report = cg.patch_cells(self.data, [edit], profile=self.profile_for_current_asset())
        second, _ = cg.patch_cells(first, [edit], profile=self.profile_for_current_asset())
        self.assertEqual(first, self.data)
        self.assertEqual(second, first)
        self.assertEqual(report["changed_cell_count"], 0)

    def test_wrong_header_hash_path_and_column_are_rejected(self):
        profile = self.profile_for_current_asset()
        changed_payload = self.data.replace("鐵騎の少女".encode(), "鐵騎の少女".encode(), 1)
        for data, profile, edit in (
            (self.data.replace(b"Touch2", b"TouchX", 1), profile, []),
            (changed_payload, profile, []),
            (self.data, {**profile, "asset_path_ids": {"Players_Japanese": 6329}}, []),
            (self.data, profile, [{"asset_path_id": 8337, "row_index": 1,
                "column": "Missing", "expected_text": "", "translation": "x"}]),
        ):
            with self.subTest(data=data[-1:], profile=profile, edit=edit):
                with self.assertRaises(cg.CellPatchError):
                    cg.patch_cells(data, edit, profile=profile)
        with self.assertRaises(cg.CellPatchError):
            cg.patch_cells(self.data, [], profile={"asset_path_ids": {"Players_Japanese": 8337}})
        with self.assertRaises(cg.CellPatchError):
            cg.parse_raw_table(self.data)
        bad_layout = {**profile, "table_layouts": [{**profile["table_layouts"][0],
                      "editable_columns": list(range(1, 27))}]}
        with self.assertRaises(cg.CellPatchError):
            cg.patch_cells(self.data, [], profile=bad_layout)
        with self.assertRaises(cg.CellPatchError):
            cg.patch_cells(self.data, [{"asset_path_id": 8337, "row_index": 1,
                "column": "Login", "expected_sha256": "0" * 64,
                "translation": "새 로그인 대사"}], profile=profile)
        with self.assertRaises(cg.CellPatchError):
            cg.patch_cells(self.data, [{"asset_path_id": 6329, "row_index": 1,
                "column": "Login", "expected_text": "鐵騎の少女",
                "translation": "새 로그인 대사"}], profile=profile)

    def test_legacy_27_column_mode_still_uses_legacy_path(self):
        rows = self.data.split(b"\r\n")
        legacy = b"\r\n".join(b",".join(cell for index, cell in enumerate(row.split(b","))
                                         if index != 26) for row in rows if row) + b"\r\n"
        self.assertEqual(cg.parse_raw_table(legacy).width, 27)
        result, report = cg.patch_cells(legacy, [], profile={"asset_path_ids": {"Players_Japanese": 6329}})
        self.assertEqual(result, legacy)
        self.assertEqual(report["asset_path_id"], 6329)

    def test_profile_factory_rejects_wrong_asset_identity(self):
        self.assertTrue(hasattr(cg, "players_touch2_profile"), "current layout profile support is missing")
        for field, value in (("name", "Players_English"), ("path_id", 6329),
                             ("sha256", "0" * 64),
                             ("catalog_paths", ["Text/Table/Other"])):
            with self.subTest(field=field), self.assertRaises(cg.CellPatchError):
                cg.players_touch2_profile({**self.asset, field: value})

