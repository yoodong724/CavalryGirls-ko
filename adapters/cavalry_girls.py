"""Byte-span cell adapter for Cavalry Girls raw-comma text tables.

The game dialect has no CSV quoting rules: every ASCII comma separates a
field, ASCII quotes are literal data, and every physical row ends in CRLF.
This module works on table bytes only.  Loading and saving Unity assets stays
the caller's responsibility.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


_PROTECTED_TOKEN_RE = re.compile(
    r"<[^<>\r\n]+>"
    r"|\{[^{}\r\n]+\}"
    r"|%(?:\d+\$)?[-+#0 ']*(?:\d+|\*)?(?:\.(?:\d+|\*))?[A-Za-z]"
    r"|\\+[nrt]"
)

_MULTILINGUAL_ASSETS = frozenset({"Descriptions", "ConditionEvents", "SpecialMod"})


def _table_dialect_module() -> Any:
    """Load the sibling dialect adapter without relying on project-root sys.path.

    ``build.py`` loads this module by absolute path when invoked as a script,
    so the project root is not necessarily importable as the ``adapters``
    package.  The sibling path is itself dependency-sealed by the profile.
    """

    module_name = "cg_cavalry_girls_table_dialect"
    cached = sys.modules.get(module_name)
    if cached is not None:
        return cached
    path = Path(__file__).resolve().with_name("table_dialect.py")
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise CellPatchError(f"cannot load sibling table dialect: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


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


def _profile_mapping(profile: Any) -> Mapping[str, Any] | None:
    """Return only the data portion of an optional, already validated profile."""
    if profile is None:
        return None
    if isinstance(profile, Mapping):
        return profile
    value = getattr(profile, "raw", None)
    if isinstance(value, Mapping):
        return value
    raise CellPatchError("profile must be a validated profile mapping")


def _profile_asset_path_id(profile: Mapping[str, Any], asset_name: str) -> int:
    """Resolve one exact update PathID from explicit, non-conflicting data."""

    candidates: list[Any] = []
    profile_ids = profile.get("asset_path_ids")
    if isinstance(profile_ids, Mapping) and asset_name in profile_ids:
        candidates.append(profile_ids[asset_name])
    assets = profile.get("text_assets")
    if isinstance(assets, list):
        for item in assets:
            if not isinstance(item, Mapping):
                continue
            header = item.get("header")
            name = item.get("name") or item.get("asset_name")
            if name is None and isinstance(header, list) and header:
                name = header[0]
            if name == asset_name:
                candidates.append(item.get("path_id", item.get("asset_path_id")))
    if not candidates:
        raise CellPatchError(f"profile asset_path_ids is missing {asset_name}")
    try:
        normalized = {
            int(value)
            for value in candidates
            if not isinstance(value, bool) and value is not None
        }
    except (TypeError, ValueError) as exc:
        raise CellPatchError(f"profile asset_path_id for {asset_name} must be an integer") from exc
    if len(normalized) != 1 or len(candidates) != sum(value is not None for value in candidates):
        raise CellPatchError(f"profile asset_path_id for {asset_name} is missing or ambiguous")
    selected = normalized.pop()
    if selected <= 0:
        raise CellPatchError(f"profile asset_path_id for {asset_name} must be positive")
    return selected


def _profile_exception(
    profile: Mapping[str, Any], *, asset_name: str, path_id: int
) -> Mapping[str, Any] | None:
    """Find the sole hash-pinned exception matching the current update asset."""

    direct = profile.get("dialect") or profile.get("table_dialect")
    exceptions = profile.get("table_exceptions")
    values: list[Any] = [direct] if isinstance(direct, Mapping) else []
    if isinstance(exceptions, Mapping):
        values.extend(exceptions.values())
    elif isinstance(exceptions, list):
        values.extend(exceptions)
    seen: set[int] = set()
    matches: list[Mapping[str, Any]] = []
    for value in values:
        if not isinstance(value, Mapping) or id(value) in seen:
            continue
        seen.add(id(value))
        value_path = value.get("asset_path_id")
        value_name = value.get("asset_name") or value.get("name")
        if value_path is None and value_name is None:
            raise CellPatchError("profile table exception has no asset identity")
        try:
            normalized_path = int(value_path) if value_path is not None else None
        except (TypeError, ValueError) as exc:
            raise CellPatchError("profile table exception asset_path_id is invalid") from exc
        if value_name == asset_name and normalized_path not in (None, path_id):
            raise CellPatchError(f"profile table exception path mismatches {asset_name}")
        if normalized_path == path_id and value_name not in (None, asset_name):
            raise CellPatchError(f"profile table exception name mismatches PathID {path_id}")
        if value_name not in (None, asset_name):
            continue
        if normalized_path not in (None, path_id):
            continue
        matches.append(value)
    if len(matches) > 1:
        raise CellPatchError(f"ambiguous profile table exceptions for {asset_name}")
    return matches[0] if matches else None


def _dialect_name(spec: Mapping[str, Any]) -> Any:
    return spec.get("name") or spec.get("dialect")


def _parse_profile_table(
    data: bytes,
    profile: Mapping[str, Any],
    *,
    validate_evidence: bool = True,
) -> RawTable:
    """Parse a native malformed table after explicit profile repairs.

    The ordinary adapter remains strict.  A version profile may opt into the
    separately evidenced native delimiter scanner and declare rows where the
    final Japanese cell is absent.  Only those rows can be repaired, and any
    other short/long row remains visible to the post-patch checks.
    """
    try:
        tokenized = _table_dialect_module().tokenizer(
            data, "native-raw-comma-newline-v1"
        )
    except Exception as exc:  # pragma: no cover - scanner gives the detail
        raise CellPatchError(f"profile dialect tokenizer rejected table: {exc}") from exc
    spec = _SPEC_BY_HEADER.get(tokenized.header)
    if spec is None:
        first = tokenized.header[0] if tokenized.header else ""
        raise CellPatchError(f"unrecognized or inexact header for {first!r}")
    spec = TableSpec(
        _profile_asset_path_id(profile, spec.asset_name),
        spec.header,
        spec.editable_columns,
    )
    dialect = _profile_exception(
        profile, asset_name=spec.asset_name, path_id=spec.asset_path_id
    )
    if dialect is None:
        strict = parse_raw_table(data)
        return RawTable(spec=spec, cells=strict.cells)
    if _dialect_name(dialect) != "native-raw-comma-newline-v1":
        raise CellPatchError("unsupported profile table dialect")
    asset_sha = dialect.get("asset_sha256")
    if validate_evidence and (not isinstance(asset_sha, str) or _sha256(data) != asset_sha):
        raise CellPatchError("profile table asset hash mismatch")
    rows = tuple(
        tuple(RawCell(cell.start, cell.end, cell.logical_text) for cell in row)
        for row in tokenized.cells
    )
    repaired = dialect.get("repaired_rows", [])
    orphan = dialect.get("orphan_rows", [])
    declared_rows = []
    for item in (*repaired, *orphan) if isinstance(repaired, list) and isinstance(orphan, list) else ():
        if isinstance(item, Mapping) and isinstance(item.get("row_index"), int):
            declared_rows.append(item["row_index"])
    allowed = dialect.get("allow_short_rows", declared_rows)
    if not isinstance(allowed, list) or any(
        not isinstance(item, int) or item <= 0 for item in allowed
    ):
        raise CellPatchError("profile dialect allow_short_rows must be positive row indexes")
    allowed_set = set(allowed)
    evidence: dict[int, Mapping[str, Any]] = {}
    for group in (repaired, orphan):
        if isinstance(group, list):
            for item in group:
                if isinstance(item, Mapping) and isinstance(item.get("row_index"), int):
                    evidence[item["row_index"]] = item
    for row_index, row in enumerate(rows):
        if len(row) != len(spec.header) and row_index not in allowed_set:
            raise CellPatchError(
                f"row {row_index} has {len(row)} fields; profile did not allow its width"
            )
        if row_index in allowed_set and validate_evidence:
            item = evidence.get(row_index)
            if item is None:
                raise CellPatchError(f"profile row {row_index} has no hash evidence")
            expected_width = item.get("input_width", item.get("width"))
            if expected_width != len(row):
                raise CellPatchError(f"profile row {row_index} width evidence mismatch")
            if item.get("key") is not None and item.get("key") != row[0].text:
                raise CellPatchError(f"profile row {row_index} key evidence mismatch")
            row_raw = data[row[0].start : row[-1].end] if row else b""
            expected_raw_sha = item.get("raw_sha256", item.get("raw_row_sha256"))
            if expected_raw_sha != _sha256(row_raw):
                raise CellPatchError(f"profile row {row_index} hash evidence mismatch")
    return RawTable(spec=spec, cells=rows)


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


def patch_cells(
    data: bytes,
    edits: Sequence[Mapping[str, Any]],
    *,
    profile: Any = None,
) -> tuple[bytes, dict[str, Any]]:
    """Apply guarded edits while preserving every byte outside target cells.

    ``profile`` is intentionally keyword-only.  With no profile this is the
    historical strict raw-comma/CRLF adapter.  A validated update profile may
    explicitly declare the native scanner and a bounded list of rows missing
    only their trailing Japanese cell; those rows are repaired by inserting
    ``,<translation>`` at the row boundary.  No quoting or general malformed
    row repair is accepted.
    """

    if isinstance(edits, (str, bytes)) or not isinstance(edits, Sequence):
        raise TypeError("edits must be a sequence of mappings")
    original_data = data
    profile_data = _profile_mapping(profile)
    profile_edits: list[Mapping[str, Any]] = []
    regular_edits: list[Mapping[str, Any]] = []
    profile_reports: list[dict[str, Any]] = []
    if profile_data is not None:
        table = _parse_profile_table(data, profile_data)
        dialect = _profile_exception(
            profile_data,
            asset_name=table.asset_name,
            path_id=table.asset_path_id,
        )
        for edit in edits:
            if isinstance(edit, Mapping) and edit.get("target_cell_present") is False:
                profile_edits.append(edit)
            else:
                regular_edits.append(edit)
        if profile_edits:
            if dialect is None:
                raise CellPatchError(
                    f"profile has no table exception for missing cells in {table.asset_name}"
                )
            repaired_rows = dialect.get("repaired_rows", [])
            if not isinstance(repaired_rows, list):
                raise CellPatchError("profile repaired_rows must be a list")
            policy_rows = {
                item.get("row_index"): item
                for item in repaired_rows
                if isinstance(item, Mapping) and isinstance(item.get("row_index"), int)
            }
            allowed_rows = set(dialect.get("allow_short_rows", policy_rows))
            insertions: list[tuple[int, int, str, str, dict[str, Any]]] = []
            for edit_number, edit in enumerate(profile_edits):
                if edit.get("asset_path_id") not in (None, table.asset_path_id):
                    raise CellPatchError(f"profile edit {edit_number}: asset_path_id mismatch")
                row_index = edit.get("row_index")
                if isinstance(row_index, bool) or not isinstance(row_index, int) or row_index not in allowed_rows:
                    raise CellPatchError(f"profile edit {edit_number}: row is not a declared short row")
                if row_index <= 0 or row_index >= table.row_count:
                    raise CellPatchError(f"profile edit {edit_number}: row index is out of range")
                if "translation" not in edit or not isinstance(edit["translation"], str):
                    raise CellPatchError(f"profile edit {edit_number}: translation is required")
                policy = policy_rows.get(row_index)
                if policy is None:
                    raise CellPatchError(f"profile edit {edit_number}: row has no repaired_rows evidence")
                if len(table.cells[row_index]) + 1 != table.width:
                    raise CellPatchError(f"profile edit {edit_number}: row is not missing one trailing cell")
                key = table.cells[row_index][0].text
                if edit.get("key") != key or policy.get("key") != key:
                    raise CellPatchError(f"profile edit {edit_number}: key does not match first column")
                if policy.get("input_width") != len(table.cells[row_index]):
                    raise CellPatchError(f"profile edit {edit_number}: input width evidence mismatch")
                raw_sha = _sha256(data[table.cells[row_index][0].start : table.cells[row_index][-1].end])
                expected_policy_sha = policy.get("raw_sha256", policy.get("raw_row_sha256"))
                if expected_policy_sha != raw_sha:
                    raise CellPatchError(f"profile edit {edit_number}: row evidence hash mismatch")
                expected = edit.get("expected_text", "")
                if expected not in ("", None):
                    raise CellPatchError(f"profile edit {edit_number}: missing cell expected_text must be empty")
                expected_sha = _guard_sha(edit, edit_number)
                if expected_sha != _text_sha256(""):
                    raise CellPatchError(
                        f"profile edit {edit_number}: missing cell expected SHA must bind an empty target"
                    )
                target_column = edit.get("column", edit.get("column_name", table.header[-1]))
                if target_column not in (table.header[-1], len(table.header) - 1) or policy.get("target_column", table.header[-1]) not in (table.header[-1], len(table.header) - 1):
                    raise CellPatchError(f"profile edit {edit_number}: missing cell must be the trailing Japanese column")
                if edit.get("control_source_column") != "Chinese" or not isinstance(edit.get("control_source_sha256"), str):
                    raise CellPatchError(f"profile edit {edit_number}: missing cell requires hashed Chinese control source")
                chinese_column = _column_index(table, "Chinese")
                control_text = table.cells[row_index][chinese_column].text
                if not control_text:
                    raise CellPatchError(f"profile edit {edit_number}: empty Chinese control source")
                if _text_sha256(control_text) != edit["control_source_sha256"]:
                    raise CellPatchError(f"profile edit {edit_number}: Chinese control source hash mismatch")
                insertion = edit.get("target_insertion")
                insertion_offset = table.cells[row_index][-1].end
                if not isinstance(insertion, Mapping):
                    raise CellPatchError(
                        f"profile edit {edit_number}: target insertion evidence is required"
                    )
                if (
                    insertion.get("dialect") != _dialect_name(dialect)
                    or insertion.get("row_index") != row_index
                    or insertion.get("column") not in (table.header[-1], len(table.header) - 1)
                    or insertion.get("original_width") != len(table.cells[row_index])
                    or insertion.get("output_width") != table.width
                    or (
                        "insertion_offset" in insertion
                        and insertion.get("insertion_offset") != insertion_offset
                    )
                    or insertion.get("inserted_value", "") != ""
                    or insertion.get("raw_row_sha256", insertion.get("raw_sha256")) != raw_sha
                ):
                    raise CellPatchError(f"profile edit {edit_number}: target insertion evidence mismatch")
                replacement = _translation_bytes(control_text, edit["translation"], row_index)
                if replacement != edit["translation"].encode("utf-8"):
                    raise CellPatchError("profile trailing-cell replacement encoding mismatch")
                insertions.append((row_index, insertion_offset, key, edit["translation"], {
                    "row_index": row_index,
                    "column_index": table.width - 1,
                    "column_name": table.header[-1],
                    "key": key,
                    "occurrence": edit.get("occurrence"),
                    "source_cell_sha256": _text_sha256(""),
                    "translation_sha256": _text_sha256(edit["translation"]),
                    "control_source": {
                        "mode": "explicit_chinese",
                        "column_name": "Chinese",
                        "column_index": chinese_column,
                        "cell_sha256": edit["control_source_sha256"],
                    },
                    "source_span": [insertion_offset, insertion_offset],
                    "source_size": 0,
                    "output_size": len(b",") + len(replacement),
                    "insertion": {**dict(insertion), "insertion_offset": insertion_offset},
                    "changed": True,
                }))
            # Descending byte order keeps every not-yet-processed original span
            # stable when a profile ever declares more than one repair.
            for row_index, _offset, key, translation, report in sorted(
                insertions, key=lambda item: item[1], reverse=True
            ):
                try:
                    dialect_module = _table_dialect_module()
                    tokenized = dialect_module.tokenizer(
                        data, "native-raw-comma-newline-v1"
                    )
                    data = dialect_module.append_trailing_cell(
                        data, tokenized, row_index, translation, expected_key=key
                    )
                except Exception as exc:
                    raise CellPatchError(f"profile trailing-cell insertion failed: {exc}") from exc
                profile_reports.append(report)
            table = _parse_profile_table(data, profile_data, validate_evidence=False)
    else:
        table = parse_raw_table(data)
        regular_edits = list(edits)
    occurrences, totals = _occurrences(table)
    replacements: list[tuple[int, int, bytes, int]] = []
    reports: list[dict[str, Any] | None] = [None] * len(regular_edits)
    intended: dict[tuple[int, int], str] = {}

    for edit_number, edit in enumerate(regular_edits):
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

    reparsed = _parse_profile_table(output, profile_data, validate_evidence=False) if profile_data is not None else parse_raw_table(output)
    if reparsed.row_count != table.row_count or reparsed.header != table.header:
        raise CellPatchError("post-patch table structure changed")
    for row_index, (before, after) in enumerate(zip(table.cells, reparsed.cells, strict=True)):
        for column_index, (old, new) in enumerate(zip(before, after, strict=True)):
            expected = intended.get((row_index, column_index), old.text)
            if new.text != expected:
                raise CellPatchError(
                    f"post-patch verification failed at row {row_index}, column {column_index}"
                )

    final_reports = profile_reports + [report for report in reports if report is not None]
    return output, {
        "dialect": "raw-ascii-comma-crlf-v3",
        "asset_name": table.asset_name,
        "asset_path_id": table.asset_path_id,
        "header": list(table.header),
        "raw_field_count": table.width,
        "logical_row_count": table.row_count,
        "input_sha256": _sha256(original_data),
        "output_sha256": _sha256(output),
        "input_size": len(original_data),
        "output_size": len(output),
        "requested_edit_count": len(edits),
        "profile_trailing_cell_count": len(profile_edits),
        "changed_cell_count": sum(bool(item["changed"]) for item in final_reports),
        "byte_identical": output == original_data,
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
