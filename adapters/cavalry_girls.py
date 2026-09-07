"""Byte-span cell adapter for Cavalry Girls raw-comma text tables.

The game dialect has no CSV quoting rules: every ASCII comma separates a
field, ASCII quotes are literal data, and every physical row ends in CRLF.
This module works on table bytes only.  Loading and saving Unity assets stays
the caller's responsibility.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


_PROTECTED_TOKEN_RE = re.compile(
    r"<[^<>\r\n]+>"
    r"|\{[^{}\r\n]+\}"
    r"|%(?:\d+\$)?[-+#0 ']*(?:\d+|\*)?(?:\.(?:\d+|\*))?[A-Za-z]"
    r"|\\+[nrt]"
)

_MULTILINGUAL_ASSETS = frozenset({"Descriptions", "ConditionEvents", "SpecialMod"})


@dataclass(frozen=True)
class TableSpec:
    asset_path_id: int
    header: tuple[str, ...]
    editable_columns: frozenset[int]

    @property
    def asset_name(self) -> str:
        return self.header[0]


@dataclass(frozen=True)
class RawCell:
    start: int
    end: int
    text: str


@dataclass(frozen=True)
class RawTable:
    """Parsed table with decoded values and original byte spans."""

    spec: TableSpec
    cells: tuple[tuple[RawCell, ...], ...]

    @property
    def asset_name(self) -> str:
        return self.spec.asset_name

    @property
    def asset_path_id(self) -> int:
        return self.spec.asset_path_id

    @property
    def header(self) -> tuple[str, ...]:
        return self.spec.header

    @property
    def width(self) -> int:
        return len(self.spec.header)

    @property
    def row_count(self) -> int:
        return len(self.cells)

    @property
    def rows(self) -> tuple[tuple[str, ...], ...]:
        return tuple(tuple(cell.text for cell in row) for row in self.cells)


class CellPatchError(ValueError):
    """Raised when a table or edit violates the confirmed game format."""


class ConstraintViolation(CellPatchError):
    """An edit was blocked by a named, machine-readable constraint policy."""

    def __init__(self, message: str, policy: Mapping[str, Any]):
        super().__init__(message)
        self.policy = dict(policy)


_SPECS = (
    TableSpec(
        6326,
        ("Descriptions", "Chinese", "ChineseTraditional", "English", "Japanese"),
        frozenset({4}),
    ),
    TableSpec(
        6427,
        (
            "ConditionEvents",
            "Chinese",
            "English",
            "Japanese",
            "ChineseTraditional",
            "ImagePath",
        ),
        frozenset({3}),
    ),
    TableSpec(
        6402,
        (
            "SpecialMod",
            "FileId",
            "Chinese",
            "ChineseTraditional",
            "English",
            "Japanese",
            "Comment",
        ),
        frozenset({5}),
    ),
    TableSpec(
        6329,
        (
            "Players_Japanese",
            "BattleRetreat",
            "Damage2",
            "Death",
            "Change",
            "ChangeSec",
            "ChangeLast",
            "ChangeSuccess",
            "VicBad",
            "VicNormal",
            "VicPerfect",
            "StartBad",
            "StartNormal",
            "StartPerfect",
            "GiftDis",
            "GiftNormal",
            "GiftLike",
            "GiftPerfect",
            "CommandRefuse",
            "Entrance",
            "Exit",
            "Refuse",
            "Meet",
            "Battle",
            "Promoting",
            "Touch",
            "Login",
        ),
        frozenset(range(1, 27)),
    ),
    TableSpec(
        6351,
        (
            "Players2_Japanese",
            "Shop",
            "ShopAi",
            "Restraunt",
            "RestrauntAi",
            "Beach",
            "BeachAi",
            "Onsen",
            "OnsenAi",
            "Cinema",
            "CinemaAi",
        ),
        frozenset(range(1, 11)),
    ),
)
_SPEC_BY_HEADER = {spec.header: spec for spec in _SPECS}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _text_sha256(text: str) -> str:
    return _sha256(text.encode("utf-8"))


def _decode(raw: bytes, row_index: int, column_index: int) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CellPatchError(
            f"row {row_index}, column {column_index} is not valid UTF-8: {exc}"
        ) from exc


def parse_raw_table(data: bytes) -> RawTable:
    """Parse one confirmed table without applying conventional CSV quoting."""

    if not isinstance(data, bytes):
        raise TypeError("data must be bytes")
    if not data:
        raise CellPatchError("table is empty")
    if data.startswith(b"\xef\xbb\xbf"):
        raise CellPatchError("UTF-8 BOM is not supported")
    if not data.endswith(b"\r\n"):
        raise CellPatchError("table must end with CRLF")
    without_crlf = data.replace(b"\r\n", b"")
    if b"\r" in without_crlf or b"\n" in without_crlf:
        raise CellPatchError("table contains a non-CRLF physical newline")

    raw_rows = data[:-2].split(b"\r\n")
    raw_header = raw_rows[0].split(b",")
    header = tuple(
        _decode(field, 0, column_index)
        for column_index, field in enumerate(raw_header)
    )
    spec = _SPEC_BY_HEADER.get(header)
    if spec is None:
        first = header[0] if header else ""
        raise CellPatchError(f"unrecognized or inexact header for {first!r}")

    rows: list[tuple[RawCell, ...]] = []
    row_start = 0
    for row_index, raw_row in enumerate(raw_rows):
        raw_fields = raw_row.split(b",")
        if len(raw_fields) != len(spec.header):
            key = raw_fields[0][:80].decode("utf-8", errors="replace") if raw_fields else ""
            raise CellPatchError(
                f"row {row_index} key {key!r} has {len(raw_fields)} fields; "
                f"header requires {len(spec.header)}"
            )
        cells: list[RawCell] = []
        field_start = row_start
        for column_index, raw_field in enumerate(raw_fields):
            field_end = field_start + len(raw_field)
            cells.append(
                RawCell(
                    start=field_start,
                    end=field_end,
                    text=_decode(raw_field, row_index, column_index),
                )
            )
            field_start = field_end + 1
        rows.append(tuple(cells))
        row_start += len(raw_row) + 2
    return RawTable(spec=spec, cells=tuple(rows))


def _column_index(table: RawTable, value: Any) -> int:
    if isinstance(value, bool):
        raise CellPatchError("column must be an exact header name or integer index")
    if isinstance(value, int):
        if value < 0 or value >= table.width:
            raise CellPatchError(f"column index {value} is out of range")
        return value
    if isinstance(value, str):
        matches = [index for index, name in enumerate(table.header) if name == value]
        if len(matches) != 1:
            raise CellPatchError(f"column name {value!r} does not occur exactly once")
        return matches[0]
    raise CellPatchError("column must be an exact header name or integer index")


def _edit_column(table: RawTable, edit: Mapping[str, Any], edit_number: int) -> int:
    selectors = []
    for name in ("column_name", "column_index", "column"):
        if name in edit:
            selectors.append((name, _column_index(table, edit[name])))
    if not selectors:
        raise CellPatchError(
            f"edit {edit_number}: one of column_name, column_index, or column is required"
        )
    indexes = {value for _, value in selectors}
    if len(indexes) != 1:
        raise CellPatchError(f"edit {edit_number}: column selectors disagree")
    column_index = indexes.pop()
    if column_index not in table.spec.editable_columns:
        raise CellPatchError(
            f"edit {edit_number}: column {table.header[column_index]!r} is not an "
            f"authorized Japanese payload column for {table.asset_name}"
        )
    return column_index


def _occurrences(table: RawTable) -> tuple[list[int | None], dict[str, int]]:
    seen: dict[str, int] = {}
    totals: dict[str, int] = {}
    values: list[int | None] = [None]
    for row in table.cells[1:]:
        key = row[0].text
        totals[key] = totals.get(key, 0) + 1
        values.append(seen.get(key, 0))
        seen[key] = seen.get(key, 0) + 1
    return values, totals


def _translation_bytes(source: str, translation: str, row_index: int) -> bytes:
    try:
        encoded = translation.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise CellPatchError(f"row {row_index}: translation is not valid UTF-8") from exc
    if b"," in encoded:
        raise ConstraintViolation(
            f"row {row_index}: translation contains an ASCII comma field separator",
            {
                "policy": "raw-comma-cell-v1",
                "status": "blocked",
                "reason": "ascii_comma_insertion",
                "row_index": row_index,
            },
        )
    if b"\r" in encoded or b"\n" in encoded:
        raise ConstraintViolation(
            f"row {row_index}: translation contains an actual CR/LF",
            {
                "policy": "raw-comma-cell-v1",
                "status": "blocked",
                "reason": "physical_newline_insertion",
                "row_index": row_index,
            },
        )

    source_tokens = _PROTECTED_TOKEN_RE.findall(source)
    target_tokens = _PROTECTED_TOKEN_RE.findall(translation)
    if source_tokens != target_tokens:
        raise ConstraintViolation(
            f"row {row_index}: protected token sequence changed",
            {
                "policy": "preserve-control-sequence-v1",
                "status": "blocked",
                "reason": "protected_token_sequence_changed",
                "row_index": row_index,
                "source_tokens": source_tokens,
                "target_tokens": target_tokens,
            },
        )
    source_parts = source.split("|")
    target_parts = translation.split("|")
    source_empty = [index for index, part in enumerate(source_parts) if not part]
    target_empty = [index for index, part in enumerate(target_parts) if not part]
    source_segment_tokens = [_PROTECTED_TOKEN_RE.findall(part) for part in source_parts]
    target_segment_tokens = [_PROTECTED_TOKEN_RE.findall(part) for part in target_parts]
    if (
        len(source_parts) != len(target_parts)
        or source_empty != target_empty
        or source_segment_tokens != target_segment_tokens
    ):
        raise ConstraintViolation(
            f"row {row_index}: pipe-delimited structure changed",
            {
                "policy": "preserve-pipe-structure-v1",
                "status": "blocked",
                "reason": "pipe_structure_changed",
                "row_index": row_index,
                "source_segment_count": len(source_parts),
                "target_segment_count": len(target_parts),
                "source_empty_segments": source_empty,
                "target_empty_segments": target_empty,
                "source_segment_tokens": source_segment_tokens,
                "target_segment_tokens": target_segment_tokens,
            },
        )
    return encoded


def _control_signature(text: str) -> dict[str, Any]:
    parts = text.split("|")
    return {
        "protected_tokens": _PROTECTED_TOKEN_RE.findall(text),
        "pipe_segment_count": len(parts),
        "empty_pipe_segments": [index for index, part in enumerate(parts) if not part],
        "segment_protected_tokens": [
            _PROTECTED_TOKEN_RE.findall(part) for part in parts
        ],
    }


def _select_control_source(
    table: RawTable,
    edit: Mapping[str, Any],
    edit_number: int,
    row_index: int,
    target_column_index: int,
) -> tuple[str, dict[str, Any]]:
    allowed_keys = {"control_source_column", "control_source_sha256"}
    unknown_keys = sorted(
        key
        for key in edit
        if isinstance(key, str)
        and key.startswith("control_source")
        and key not in allowed_keys
    )
    if unknown_keys:
        raise CellPatchError(
            f"edit {edit_number}: unsupported control source fields {unknown_keys}"
        )

    has_column = "control_source_column" in edit
    has_hash = "control_source_sha256" in edit
    if has_column != has_hash:
        raise CellPatchError(
            f"edit {edit_number}: control_source_column and "
            "control_source_sha256 must be supplied together"
        )

    target_cell = table.cells[row_index][target_column_index]
    if not has_column:
        return target_cell.text, {
            "mode": "target_japanese",
            "column_name": table.header[target_column_index],
            "column_index": target_column_index,
            "cell_sha256": _text_sha256(target_cell.text),
        }

    if table.asset_name not in _MULTILINGUAL_ASSETS:
        raise CellPatchError(
            f"edit {edit_number}: control source override is not allowed for "
            f"{table.asset_name}"
        )
    if edit["control_source_column"] != "Chinese":
        raise CellPatchError(
            f"edit {edit_number}: control_source_column must be exactly 'Chinese'"
        )
    chinese_column_index = _column_index(table, "Chinese")
    chinese_cell = table.cells[row_index][chinese_column_index]
    if not chinese_cell.text:
        raise CellPatchError(
            f"row {row_index}: empty Chinese control source requires Japanese fallback"
        )
    supplied_hash = edit["control_source_sha256"]
    actual_hash = _text_sha256(chinese_cell.text)
    if not isinstance(supplied_hash, str) or supplied_hash != actual_hash:
        raise CellPatchError(
            f"row {row_index}: control_source_sha256 does not match Chinese cell"
        )
    return chinese_cell.text, {
        "mode": "explicit_chinese",
        "column_name": "Chinese",
        "column_index": chinese_column_index,
        "cell_sha256": actual_hash,
    }


def _control_difference(control_text: str, japanese_text: str) -> dict[str, Any]:
    control = _control_signature(control_text)
    japanese = _control_signature(japanese_text)
    return {
        "differs": control != japanese,
        "protected_tokens_equal": (
            control["protected_tokens"] == japanese["protected_tokens"]
        ),
        "pipe_structure_equal": (
            control["pipe_segment_count"] == japanese["pipe_segment_count"]
            and control["empty_pipe_segments"] == japanese["empty_pipe_segments"]
        ),
        "segment_protected_tokens_equal": (
            control["segment_protected_tokens"]
            == japanese["segment_protected_tokens"]
        ),
        "control_signature": control,
        "japanese_signature": japanese,
    }


def _guard_occurrence(edit: Mapping[str, Any], edit_number: int) -> Any:
    values = []
    for name in ("occurrence", "key_occurrence"):
        if name in edit:
            values.append(edit[name])
    if len(values) == 2 and values[0] != values[1]:
        raise CellPatchError(f"edit {edit_number}: occurrence guards disagree")
    return values[0] if values else None


def _guard_sha(edit: Mapping[str, Any], edit_number: int) -> Any:
    values = []
    for name in ("expected_sha256", "expected_sha"):
        if name in edit:
            values.append(edit[name])
    if len(values) == 2 and values[0] != values[1]:
        raise CellPatchError(f"edit {edit_number}: expected SHA guards disagree")
    return values[0] if values else None


def _locator_json(
    table: RawTable, key: str, occurrence: int, column_index: int
) -> str:
    return json.dumps(
        {
            "asset_path_id": table.asset_path_id,
            "key": key,
            "occurrence": occurrence,
            "column": table.header[column_index],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def resolve_locator(data: bytes, locator: str | Mapping[str, Any]) -> dict[str, Any]:
    """Resolve a stable JSON locator to the current row and guarded cell."""

    table = parse_raw_table(data)
    if isinstance(locator, str):
        try:
            parsed = json.loads(locator)
        except json.JSONDecodeError as exc:
            raise CellPatchError(f"locator is not valid JSON: {exc}") from exc
    elif isinstance(locator, Mapping):
        parsed = dict(locator)
    else:
        raise TypeError("locator must be a JSON string or mapping")
    if not isinstance(parsed, dict):
        raise CellPatchError("locator JSON must contain an object")
    required = {"asset_path_id", "key", "occurrence", "column"}
    missing = sorted(required - parsed.keys())
    if missing:
        raise CellPatchError(f"locator is missing {missing}")
    if parsed["asset_path_id"] != table.asset_path_id:
        raise CellPatchError("locator asset_path_id does not match the parsed table")
    key = parsed["key"]
    occurrence = parsed["occurrence"]
    if not isinstance(key, str):
        raise CellPatchError("locator key must be a string")
    if isinstance(occurrence, bool) or not isinstance(occurrence, int) or occurrence < 0:
        raise CellPatchError("locator occurrence must be a non-negative integer")
    column_index = _column_index(table, parsed["column"])
    if column_index not in table.spec.editable_columns:
        raise CellPatchError("locator column is not an authorized Japanese payload column")
    matches = [
        row_index
        for row_index, row in enumerate(table.cells[1:], start=1)
        if row[0].text == key
    ]
    if occurrence >= len(matches):
        raise CellPatchError("locator key occurrence does not exist")
    row_index = matches[occurrence]
    text = table.cells[row_index][column_index].text
    return {
        "asset_path_id": table.asset_path_id,
        "asset_name": table.asset_name,
        "row_index": row_index,
        "column_index": column_index,
        "column_name": table.header[column_index],
        "key": key,
        "occurrence": occurrence,
        "text": text,
        "sha256": _text_sha256(text),
    }


def patch_cells(data: bytes, edits: Sequence[Mapping[str, Any]]) -> tuple[bytes, dict[str, Any]]:
    """Apply guarded edits while preserving every byte outside target cells."""

    if isinstance(edits, (str, bytes)) or not isinstance(edits, Sequence):
        raise TypeError("edits must be a sequence of mappings")
    table = parse_raw_table(data)
    occurrences, totals = _occurrences(table)
    replacements: list[tuple[int, int, bytes, int]] = []
    reports: list[dict[str, Any] | None] = [None] * len(edits)
    intended: dict[tuple[int, int], str] = {}

    for edit_number, edit in enumerate(edits):
        if not isinstance(edit, Mapping):
            raise TypeError(f"edit {edit_number} must be a mapping")
        if (
            "asset_path_id" in edit
            and edit["asset_path_id"] != table.asset_path_id
        ):
            raise CellPatchError(
                f"edit {edit_number}: asset_path_id does not match {table.asset_name}"
            )
        if "row_index" not in edit or "translation" not in edit:
            raise CellPatchError(
                f"edit {edit_number}: row_index and translation are required"
            )
        row_index = edit["row_index"]
        if isinstance(row_index, bool) or not isinstance(row_index, int):
            raise CellPatchError(f"edit {edit_number}: row_index must be an integer")
        if row_index <= 0 or row_index >= table.row_count:
            raise CellPatchError(f"edit {edit_number}: row_index {row_index} is out of range")
        column_index = _edit_column(table, edit, edit_number)
        identity = (row_index, column_index)
        if identity in intended:
            raise CellPatchError(
                f"duplicate edit for row {row_index}, column {column_index}"
            )

        cell = table.cells[row_index][column_index]
        source = cell.text
        translation = edit["translation"]
        if not isinstance(translation, str):
            raise CellPatchError(f"edit {edit_number}: translation must be a string")
        if "expected_text" not in edit and _guard_sha(edit, edit_number) is None:
            raise CellPatchError(
                f"edit {edit_number}: expected_text or expected_sha256 is required"
            )
        if "expected_text" in edit:
            if not isinstance(edit["expected_text"], str):
                raise CellPatchError(f"edit {edit_number}: expected_text must be a string")
            if edit["expected_text"] != source:
                raise CellPatchError(f"row {row_index}: expected_text does not match source")
        source_sha = _text_sha256(source)
        expected_sha = _guard_sha(edit, edit_number)
        if expected_sha is not None:
            if not isinstance(expected_sha, str) or expected_sha != source_sha:
                raise CellPatchError(f"row {row_index}: expected SHA does not match source")

        key = table.cells[row_index][0].text
        occurrence = occurrences[row_index]
        if "key" in edit:
            if not isinstance(edit["key"], str) or edit["key"] != key:
                raise CellPatchError(f"row {row_index}: key does not match first column")
            supplied_occurrence = _guard_occurrence(edit, edit_number)
            if totals[key] > 1 and supplied_occurrence is None:
                raise CellPatchError(
                    f"row {row_index}: duplicate key requires an occurrence guard"
                )
        else:
            supplied_occurrence = _guard_occurrence(edit, edit_number)
            if supplied_occurrence is not None:
                raise CellPatchError(f"edit {edit_number}: occurrence requires a key guard")
        if supplied_occurrence is not None:
            if (
                isinstance(supplied_occurrence, bool)
                or not isinstance(supplied_occurrence, int)
                or supplied_occurrence != occurrence
            ):
                raise CellPatchError(f"row {row_index}: occurrence does not match")

        control_text, control_source = _select_control_source(
            table, edit, edit_number, row_index, column_index
        )
        replacement = _translation_bytes(control_text, translation, row_index)
        intended[identity] = translation
        replacements.append((cell.start, cell.end, replacement, edit_number))
        reports[edit_number] = {
            "row_index": row_index,
            "column_index": column_index,
            "column_name": table.header[column_index],
            "key": key,
            "occurrence": occurrence,
            "locator": _locator_json(table, key, occurrence, column_index),
            "source_cell_sha256": source_sha,
            "translation_sha256": _text_sha256(translation),
            "control_source": control_source,
            "control_source_vs_japanese": _control_difference(control_text, source),
            "source_span": [cell.start, cell.end],
            "source_size": cell.end - cell.start,
            "output_size": len(replacement),
            "changed": replacement != data[cell.start : cell.end],
        }

    chunks: list[bytes] = []
    cursor = 0
    for start, end, replacement, _ in sorted(replacements):
        chunks.append(data[cursor:start])
        chunks.append(replacement)
        cursor = end
    chunks.append(data[cursor:])
    output = b"".join(chunks)

    reparsed = parse_raw_table(output)
    if reparsed.row_count != table.row_count or reparsed.header != table.header:
        raise CellPatchError("post-patch table structure changed")
    for row_index, (before, after) in enumerate(zip(table.cells, reparsed.cells, strict=True)):
        for column_index, (old, new) in enumerate(zip(before, after, strict=True)):
            expected = intended.get((row_index, column_index), old.text)
            if new.text != expected:
                raise CellPatchError(
                    f"post-patch verification failed at row {row_index}, column {column_index}"
                )

    final_reports = [report for report in reports if report is not None]
    return output, {
        "dialect": "raw-ascii-comma-crlf-v3",
        "asset_name": table.asset_name,
        "asset_path_id": table.asset_path_id,
        "header": list(table.header),
        "raw_field_count": table.width,
        "logical_row_count": table.row_count,
        "input_sha256": _sha256(data),
        "output_sha256": _sha256(output),
        "input_size": len(data),
        "output_size": len(output),
        "requested_edit_count": len(edits),
        "changed_cell_count": sum(bool(item["changed"]) for item in final_reports),
        "byte_identical": output == data,
        "constraint_policy": {
            "name": "cavalry-girls-cell-constraints-v1",
            "ascii_comma": "reject",
            "physical_cr_lf": "reject",
            "protected_token_sequence": "preserve_exactly",
            "literal_escape_backslash_count": "preserve_exactly",
            "pipe_segment_count_and_empty_positions": "preserve_exactly",
            "control_source_override": "hashed_same_row_chinese_multilingual_only",
            "quotes": "literal_data",
            "authorized_columns_only": True,
        },
        "edits": final_reports,
    }


__all__ = [
    "CellPatchError",
    "ConstraintViolation",
    "RawCell",
    "RawTable",
    "parse_raw_table",
    "patch_cells",
    "resolve_locator",
]
