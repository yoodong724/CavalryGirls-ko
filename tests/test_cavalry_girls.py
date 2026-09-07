import hashlib
import json
import unittest

from adapters.cavalry_girls import (
    CellPatchError,
    ConstraintViolation,
    parse_raw_table,
    patch_cells,
    resolve_locator,
)


DESCRIPTIONS_HEADER = (
    b"Descriptions,Chinese,ChineseTraditional,English,Japanese\r\n"
)
CONDITION_HEADER = (
    b"ConditionEvents,Chinese,English,Japanese,ChineseTraditional,ImagePath\r\n"
)
SPECIAL_MOD_HEADER = (
    b"SpecialMod,FileId,Chinese,ChineseTraditional,English,Japanese,Comment\r\n"
)
PLAYERS_HEADER = (
    b"Players_Japanese,BattleRetreat,Damage2,Death,Change,ChangeSec,ChangeLast,"
    b"ChangeSuccess,VicBad,VicNormal,VicPerfect,StartBad,StartNormal,StartPerfect,"
    b"GiftDis,GiftNormal,GiftLike,GiftPerfect,CommandRefuse,Entrance,Exit,Refuse,"
    b"Meet,Battle,Promoting,Touch,Login\r\n"
)
PLAYERS2_HEADER = (
    b"Players2_Japanese,Shop,ShopAi,Restraunt,RestrauntAi,Beach,BeachAi,Onsen,"
    b"OnsenAi,Cinema,CinemaAi\r\n"
)


class ParseTests(unittest.TestCase):
    def test_exact_headers_and_widths(self):
        fixtures = [
            (DESCRIPTIONS_HEADER + b"k,zh,zht,en,ja\r\n", "Descriptions", 5),
            (CONDITION_HEADER + b"k,zh,en,ja,zht,path\r\n", "ConditionEvents", 6),
            (SPECIAL_MOD_HEADER + b"k,id,zh,zht,en,ja,note\r\n", "SpecialMod", 7),
            (PLAYERS_HEADER + b"p," + b",".join([b"ja"] * 26) + b"\r\n", "Players_Japanese", 27),
            (PLAYERS2_HEADER + b"p," + b",".join([b"ja"] * 10) + b"\r\n", "Players2_Japanese", 11),
        ]
        for data, name, width in fixtures:
            with self.subTest(name=name):
                parsed = parse_raw_table(data)
                self.assertEqual(parsed.asset_name, name)
                self.assertEqual(parsed.width, width)
                self.assertEqual(parsed.row_count, 2)

    def test_quotes_are_literal_and_commas_still_split(self):
        table = DESCRIPTIONS_HEADER + b'k,"zh",zht,en,"ja"\r\n'
        self.assertEqual(parse_raw_table(table).rows[1][4], '"ja"')
        with self.assertRaises(CellPatchError):
            parse_raw_table(DESCRIPTIONS_HEADER + b'k,zh,zht,en,"ja,part"\r\n')

    def test_rejects_bom_newline_and_inexact_header(self):
        with self.assertRaises(CellPatchError):
            parse_raw_table(b"\xef\xbb\xbf" + DESCRIPTIONS_HEADER)
        with self.assertRaises(CellPatchError):
            parse_raw_table(DESCRIPTIONS_HEADER.replace(b"\r\n", b"\n"))
        with self.assertRaises(CellPatchError):
            parse_raw_table(DESCRIPTIONS_HEADER.replace(b"Japanese", b"japanese"))


class PatchTests(unittest.TestCase):
    def setUp(self):
        self.data = (
            DESCRIPTIONS_HEADER
            + "dup,中,繁,Hello <b>{0}\\n%s,こんにちは <b>{0}\\n%s\r\n".encode()
            + "dup,中2,繁2,One|Two,一|二\r\n".encode()
        )

    def test_span_patch_preserves_other_bytes_and_supports_aliases(self):
        source = "こんにちは <b>{0}\\n%s"
        target = "안녕하세요 <b>{0}\\n%s"
        output, report = patch_cells(
            self.data,
            [
                {
                    "row_index": 1,
                    "column_name": "Japanese",
                    "expected_sha": hashlib.sha256(source.encode()).hexdigest(),
                    "key": "dup",
                    "occurrence": 0,
                    "translation": target,
                }
            ],
        )
        before_start = self.data.index(source.encode())
        after_end = before_start + len(source.encode())
        self.assertEqual(output[:before_start], self.data[:before_start])
        self.assertEqual(output[before_start + len(target.encode()):], self.data[after_end:])
        self.assertEqual(report["changed_cell_count"], 1)
        self.assertEqual(report["constraint_policy"]["quotes"], "literal_data")
        self.assertEqual(parse_raw_table(output).rows[1][4], target)

    def test_noop_is_byte_identical(self):
        source = "一|二"
        output, report = patch_cells(
            self.data,
            [{
                "row_index": 2,
                "column_index": 4,
                "expected_text": source,
                "key": "dup",
                "key_occurrence": 1,
                "translation": source,
            }],
        )
        self.assertEqual(output, self.data)
        self.assertTrue(report["byte_identical"])
        self.assertEqual(report["changed_cell_count"], 0)

    def test_rejects_unapproved_columns_and_stale_guards(self):
        base = {
            "row_index": 1,
            "expected_text": "こんにちは <b>{0}\\n%s",
            "translation": "안녕하세요 <b>{0}\\n%s",
        }
        with self.assertRaises(CellPatchError):
            patch_cells(self.data, [{**base, "column_name": "English"}])
        with self.assertRaises(CellPatchError):
            patch_cells(self.data, [{**base, "column_name": "Japanese", "key": "wrong"}])
        with self.assertRaises(CellPatchError):
            patch_cells(self.data, [{**base, "column_name": "Japanese", "key": "dup"}])
        with self.assertRaises(CellPatchError):
            patch_cells(self.data, [{
                **base,
                "asset_path_id": 6427,
                "column_name": "Japanese",
            }])

    def test_constraint_failures_have_explicit_policy(self):
        source = "こんにちは <b>{0}\\n%s"
        cases = [
            ("쉼표, 삽입 <b>{0}\\n%s", "ascii_comma_insertion"),
            ("실제\n줄바꿈 <b>{0}\\n%s", "physical_newline_insertion"),
            ("토큰 <b>{1}\\n%s", "protected_token_sequence_changed"),
        ]
        for translation, reason in cases:
            with self.subTest(reason=reason), self.assertRaises(ConstraintViolation) as caught:
                patch_cells(self.data, [{
                    "row_index": 1,
                    "column_name": "Japanese",
                    "expected_text": source,
                    "translation": translation,
                }])
            self.assertEqual(caught.exception.policy["status"], "blocked")
            self.assertEqual(caught.exception.policy["reason"], reason)

    def test_pipe_structure_is_preserved(self):
        with self.assertRaises(ConstraintViolation) as caught:
            patch_cells(self.data, [{
                "row_index": 2,
                "column": "Japanese",
                "expected_text": "一|二",
                "translation": "하나둘",
            }])
        self.assertEqual(caught.exception.policy["reason"], "pipe_structure_changed")

        tagged = PLAYERS2_HEADER + b"p,<b>one|two</b>,,,,,,,,,\r\n"
        with self.assertRaises(ConstraintViolation):
            patch_cells(tagged, [{
                "row_index": 1,
                "column_name": "Shop",
                "expected_text": "<b>one|two</b>",
                "translation": "하나|<b>둘</b>",
            }])

    def test_players_payload_allowed_but_key_column_blocked(self):
        data = PLAYERS2_HEADER + b"p," + b",".join([b"ja"] * 10) + b"\r\n"
        output, _ = patch_cells(data, [{
            "row_index": 1,
            "column_name": "ShopAi",
            "expected_text": "ja",
            "translation": "한국어",
        }])
        self.assertEqual(parse_raw_table(output).rows[1][2], "한국어")
        with self.assertRaises(CellPatchError):
            patch_cells(data, [{
                "row_index": 1,
                "column_index": 0,
                "expected_text": "p",
                "translation": "키",
            }])

    def test_locator_json_roundtrip_and_asset_guard(self):
        locator = json.dumps({
            "asset_path_id": 6326,
            "key": "dup",
            "occurrence": 1,
            "column": "Japanese",
        })
        resolved = resolve_locator(self.data, locator)
        self.assertEqual(resolved["row_index"], 2)
        self.assertEqual(resolved["text"], "一|二")
        with self.assertRaises(CellPatchError):
            resolve_locator(self.data, {**json.loads(locator), "asset_path_id": 6427})

    def test_explicit_chinese_control_source_and_unchanged_non_target_bytes(self):
        data = (
            DESCRIPTIONS_HEADER
            + "k,中文<color=red>{0}\\n,繁體,en,日本語<b>{1}</b>\r\n".encode()
        )
        japanese = "日本語<b>{1}</b>"
        chinese = "中文<color=red>{0}\\n"
        translation = "한국어<color=red>{0}\\n"
        base = {
            "row_index": 1,
            "column_name": "Japanese",
            "expected_text": japanese,
            "translation": translation,
        }
        with self.assertRaises(ConstraintViolation):
            patch_cells(data, [base])

        output, report = patch_cells(data, [{
            **base,
            "control_source_column": "Chinese",
            "control_source_sha256": hashlib.sha256(chinese.encode()).hexdigest(),
        }])
        edit_report = report["edits"][0]
        span = edit_report["source_span"]
        self.assertEqual(output[:span[0]], data[:span[0]])
        self.assertEqual(
            output[span[0] + len(translation.encode()):],
            data[span[1]:],
        )
        self.assertEqual(edit_report["control_source"]["mode"], "explicit_chinese")
        self.assertTrue(edit_report["control_source_vs_japanese"]["differs"])
        self.assertFalse(
            edit_report["control_source_vs_japanese"]["protected_tokens_equal"]
        )

    def test_chinese_control_source_hash_and_column_are_strict(self):
        data = DESCRIPTIONS_HEADER + "k,中文\\n,繁,en,日本語\r\n".encode()
        base = {
            "row_index": 1,
            "column_name": "Japanese",
            "expected_text": "日本語",
            "translation": "한국어\\n",
        }
        with self.assertRaises(CellPatchError):
            patch_cells(data, [{
                **base,
                "control_source_column": "Chinese",
                "control_source_sha256": "0" * 64,
            }])
        with self.assertRaises(CellPatchError):
            patch_cells(data, [{
                **base,
                "control_source_column": "English",
                "control_source_sha256": hashlib.sha256(b"en").hexdigest(),
            }])
        with self.assertRaises(CellPatchError):
            patch_cells(data, [{
                **base,
                "control_source_text": "中文\\n",
            }])
        with self.assertRaises(CellPatchError):
            patch_cells(data, [{
                **base,
                "control_source_column": "Chinese",
            }])

    def test_chinese_control_source_is_allowed_in_all_multilingual_tables(self):
        fixtures = [
            (
                DESCRIPTIONS_HEADER + "k,中\\n,繁,en,日\r\n".encode(),
                "Japanese",
            ),
            (
                CONDITION_HEADER + "k,中\\n,en,日,繁,path\r\n".encode(),
                "Japanese",
            ),
            (
                SPECIAL_MOD_HEADER + "k,id,中\\n,繁,en,日,note\r\n".encode(),
                "Japanese",
            ),
        ]
        chinese = "中\\n"
        for data, column in fixtures:
            with self.subTest(asset=parse_raw_table(data).asset_name):
                output, report = patch_cells(data, [{
                    "row_index": 1,
                    "column_name": column,
                    "expected_text": "日",
                    "control_source_column": "Chinese",
                    "control_source_sha256": hashlib.sha256(chinese.encode()).hexdigest(),
                    "translation": "한\\n",
                }])
                self.assertEqual(report["edits"][0]["control_source"]["column_name"], "Chinese")
                self.assertIn("한\\n", parse_raw_table(output).rows[1])

    def test_chinese_control_source_rejected_for_parallel_asset(self):
        data = PLAYERS2_HEADER + b"p," + b",".join([b"ja"] * 10) + b"\r\n"
        with self.assertRaises(CellPatchError):
            patch_cells(data, [{
                "row_index": 1,
                "column_name": "Shop",
                "expected_text": "ja",
                "translation": "한국어",
                "control_source_column": "Chinese",
                "control_source_sha256": hashlib.sha256(b"anything").hexdigest(),
            }])

    def test_chinese_tokens_cannot_be_lost(self):
        chinese = "中文<b>{0}\\n"
        data = DESCRIPTIONS_HEADER + f"k,{chinese},繁,en,日本語\r\n".encode()
        with self.assertRaises(ConstraintViolation) as caught:
            patch_cells(data, [{
                "row_index": 1,
                "column_name": "Japanese",
                "expected_text": "日本語",
                "control_source_column": "Chinese",
                "control_source_sha256": hashlib.sha256(chinese.encode()).hexdigest(),
                "translation": "한국어<b>{0}",
            }])
        self.assertEqual(
            caught.exception.policy["reason"], "protected_token_sequence_changed"
        )

    def test_explicit_chinese_source_can_fill_empty_japanese(self):
        chinese = "中文\\n"
        data = DESCRIPTIONS_HEADER + f"k,{chinese},繁,en,\r\n".encode()
        output, report = patch_cells(data, [{
            "row_index": 1,
            "column_name": "Japanese",
            "expected_text": "",
            "control_source_column": "Chinese",
            "control_source_sha256": hashlib.sha256(chinese.encode()).hexdigest(),
            "translation": "한국어\\n",
        }])
        self.assertEqual(parse_raw_table(output).rows[1][4], "한국어\\n")
        self.assertTrue(report["edits"][0]["control_source_vs_japanese"]["differs"])

    def test_empty_chinese_requires_default_japanese_fallback(self):
        data = DESCRIPTIONS_HEADER + "k,,繁,en,日本語\\n\r\n".encode()
        with self.assertRaises(CellPatchError):
            patch_cells(data, [{
                "row_index": 1,
                "column_name": "Japanese",
                "expected_text": "日本語\\n",
                "control_source_column": "Chinese",
                "control_source_sha256": hashlib.sha256(b"").hexdigest(),
                "translation": "한국어",
            }])
        output, _ = patch_cells(data, [{
            "row_index": 1,
            "column_name": "Japanese",
            "expected_text": "日本語\\n",
            "translation": "한국어\\n",
        }])
        self.assertEqual(parse_raw_table(output).rows[1][4], "한국어\\n")

    def test_literal_escape_backslash_count_is_exact(self):
        for source, translation in [
            (r"日本語\n", r"한국어\\n"),
            (r"日本語\\n", r"한국어\n"),
        ]:
            data = DESCRIPTIONS_HEADER + f"k,中,繁,en,{source}\r\n".encode()
            with self.subTest(source=source), self.assertRaises(ConstraintViolation):
                patch_cells(data, [{
                    "row_index": 1,
                    "column_name": "Japanese",
                    "expected_text": source,
                    "translation": translation,
                }])


if __name__ == "__main__":
    unittest.main()
