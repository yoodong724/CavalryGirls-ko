"""Byte-span tokenizer matching Cavalry Girls' native table scanner.

The scanner is deliberately not a CSV parser.  ASCII comma separates every
cell, CR or LF ends every row, CRLF is consumed as one row delimiter, and a
double quote is ordinary cell data.  Spans exclude delimiters and refer to the
original UTF-8 byte string.
"""

from __future__ import annotations

from dataclasses import dataclass


NATIVE_RAW_COMMA_NEWLINE_V1 = "native-raw-comma-newline-v1"


class DialectError(ValueError):
    """Input cannot be represented by the selected confirmed dialect."""


@dataclass(frozen=True)
class DialectCell:
    start: int
    end: int
    raw: bytes
    logical_text: str


@dataclass(frozen=True)
class TokenizedTable:
    dialect: str
    header: tuple[str, ...]
    cells: tuple[tuple[DialectCell, ...], ...]
    row_delimiters: tuple[bytes, ...]
    source_size: int
    quotes_are_literal: bool = True

    @property
    def rows(self) -> tuple[tuple[str, ...], ...]:
        return tuple(tuple(cell.logical_text for cell in row) for row in self.cells)

    @property
    def widths(self) -> tuple[int, ...]:
        return tuple(len(row) for row in self.cells)


def _cell(raw: bytes, start: int, end: int, row: int, column: int) -> DialectCell:
    payload = raw[start:end]
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DialectError(
            f"row {row}, column {column} is not valid UTF-8: {exc}"
        ) from exc
    return DialectCell(start=start, end=end, raw=payload, logical_text=text)


def tokenizer(
    raw: bytes, dialect: str = NATIVE_RAW_COMMA_NEWLINE_V1
) -> TokenizedTable:
    """Tokenize table bytes exactly as the confirmed native delimiter scanner.

    The function does not repair width mismatches or interpret quoting.  This
    makes malformed native rows observable to callers instead of silently
    converting them into a different format.
    """

    if not isinstance(raw, bytes):
        raise TypeError("raw must be bytes")
    if dialect != NATIVE_RAW_COMMA_NEWLINE_V1:
        raise DialectError(f"unsupported table dialect: {dialect!r}")
    if not raw:
        raise DialectError("table is empty")

    rows: list[tuple[DialectCell, ...]] = []
    delimiters: list[bytes] = []
    current: list[DialectCell] = []
    cell_start = 0
    index = 0
    row_index = 0
    while index < len(raw):
        byte = raw[index]
        if byte == 0x2C:  # comma
            current.append(_cell(raw, cell_start, index, row_index, len(current)))
            index += 1
            cell_start = index
            continue
        if byte in (0x0D, 0x0A):
            current.append(_cell(raw, cell_start, index, row_index, len(current)))
            if byte == 0x0D and index + 1 < len(raw) and raw[index + 1] == 0x0A:
                delimiter = raw[index : index + 2]
                index += 2
            else:
                delimiter = raw[index : index + 1]
                index += 1
            rows.append(tuple(current))
            delimiters.append(delimiter)
            current = []
            cell_start = index
            row_index += 1
            continue
        index += 1

    if current or cell_start < len(raw):
        current.append(_cell(raw, cell_start, len(raw), row_index, len(current)))
        rows.append(tuple(current))
        delimiters.append(b"")

    if not rows:
        raise DialectError("table contains no native rows")
    header = tuple(cell.logical_text for cell in rows[0])
    return TokenizedTable(
        dialect=dialect,
        header=header,
        cells=tuple(rows),
        row_delimiters=tuple(delimiters),
        source_size=len(raw),
    )


def replace_span(raw: bytes, cell: DialectCell, replacement: str) -> bytes:
    """Replace one tokenized cell while preserving every byte outside its span."""

    if not isinstance(raw, bytes):
        raise TypeError("raw must be bytes")
    if not isinstance(replacement, str):
        raise TypeError("replacement must be str")
    if not (0 <= cell.start <= cell.end <= len(raw)):
        raise DialectError("cell span is outside the supplied source")
    if raw[cell.start : cell.end] != cell.raw:
        raise DialectError("cell span is stale for the supplied source")
    encoded = replacement.encode("utf-8")
    if b"," in encoded or b"\r" in encoded or b"\n" in encoded:
        raise DialectError("replacement contains a native comma/newline delimiter")
    return raw[: cell.start] + encoded + raw[cell.end :]


def append_trailing_cell(
    raw: bytes,
    table: TokenizedTable,
    row_index: int,
    value: str,
    *,
    expected_key: str,
) -> bytes:
    """Append exactly one missing final cell before a row delimiter.

    This is the bounded repair for a native row whose width is exactly one less
    than its header.  Existing cells and all following rows remain byte-for-byte
    unchanged; the inserted bytes are one comma followed by the new cell.
    """

    if not isinstance(row_index, int) or isinstance(row_index, bool):
        raise TypeError("row_index must be an integer")
    if row_index <= 0 or row_index >= len(table.cells):
        raise DialectError("row_index must select a data row")
    if table.source_size != len(raw):
        raise DialectError("tokenized table size does not match the supplied source")
    row = table.cells[row_index]
    if not row or row[0].logical_text != expected_key:
        raise DialectError("expected_key does not match the selected native row")
    if len(row) + 1 != len(table.header):
        raise DialectError("selected row is not missing exactly one trailing cell")
    if not table.row_delimiters[row_index]:
        raise DialectError("selected row has no terminating delimiter")
    insertion = row[-1].end
    if raw[insertion : insertion + len(table.row_delimiters[row_index])] != table.row_delimiters[row_index]:
        raise DialectError("row delimiter span is stale for the supplied source")
    encoded = value.encode("utf-8")
    if b"," in encoded or b"\r" in encoded or b"\n" in encoded:
        raise DialectError("appended cell contains a native comma/newline delimiter")

    output = raw[:insertion] + b"," + encoded + raw[insertion:]
    checked = tokenizer(output, table.dialect)
    if len(checked.cells) != len(table.cells):
        raise DialectError("post-insert native row count changed")
    if checked.widths[row_index] != len(table.header):
        raise DialectError("post-insert target row width is not the header width")
    for index, (before, after) in enumerate(zip(table.rows, checked.rows, strict=True)):
        if index == row_index:
            if after[:-1] != before or after[-1] != value:
                raise DialectError("post-insert target row verification failed")
        elif after != before:
            raise DialectError(f"post-insert non-target row {index} changed")
    return output


__all__ = [
    "DialectCell",
    "DialectError",
    "NATIVE_RAW_COMMA_NEWLINE_V1",
    "TokenizedTable",
    "append_trailing_cell",
    "replace_span",
    "tokenizer",
]
