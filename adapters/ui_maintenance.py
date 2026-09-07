"""Exact-object UI repairs for the screenshot maintenance candidate.

The versioned recipe contains extracted source bytes and reviewed field edits.
Only those byte spans may change; unknown objects and overlapping edits fail.
"""

from __future__ import annotations

import hashlib
from io import BytesIO
import struct
from typing import Any

from fontTools.ttLib import TTFont


class UiPatchError(RuntimeError):
    pass


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def unity_string(text: str) -> bytes:
    if not isinstance(text, str) or not text or "\0" in text or "\r" in text:
        raise UiPatchError("invalid UI string")
    data = text.encode("utf-8")
    return struct.pack("<i", len(data)) + data + b"\0" * (-len(data) % 4)


def expected_payload(record: dict[str, Any]) -> tuple[bytes, bytes]:
    source = bytes.fromhex(record["source_hex"])
    if digest(source) != record["source_sha256"]:
        raise UiPatchError("recipe source object hash mismatch")
    edits = []
    for field in record["fields"]:
        kind = field["type"]
        if kind == "string":
            before, after = unity_string(field["before"]), unity_string(field["after"])
        elif kind == "float32":
            before, after = (struct.pack("<f", field[key]) for key in ("before", "after"))
        elif kind == "pptr":
            before, after = (struct.pack("<iq", *field[key]) for key in ("before", "after"))
        else:
            raise UiPatchError(f"unsupported UI field kind: {kind}")
        offset = field["offset"]
        if not isinstance(offset, int) or offset < 0 or source[offset:offset + len(before)] != before:
            raise UiPatchError(f"source field mismatch at {offset}")
        edits.append((offset, before, after))
    edits.sort(key=lambda item: item[0])
    if not edits:
        raise UiPatchError("empty UI repair")
    for previous, following in zip(edits, edits[1:]):
        if previous[0] + len(previous[1]) > following[0]:
            raise UiPatchError("overlapping UI field edits")
    result = source
    for offset, before, after in reversed(edits):
        result = result[:offset] + after + result[offset + len(before):]
    if result == source or len(result) % 4:
        raise UiPatchError("UI repair is unchanged or misaligned")
    return source, result


def validate_font(environment: Any, recipe: dict[str, Any]) -> dict[str, Any]:
    spec = recipe["raw_font"]
    obj = environment.file.objects[spec["path_id"]]
    if obj.type.name != "Font":
        raise UiPatchError("raw Korean font object has wrong type")
    font = obj.read()
    data = bytes(font.m_FontData)
    if font.m_Name != spec["name"] or digest(data) != spec["sha256"]:
        raise UiPatchError("raw Korean font identity/hash mismatch")
    parsed = TTFont(BytesIO(data))
    try:
        cmap = parsed.getBestCmap() or {}
        metrics = parsed["hmtx"].metrics
        units = parsed["head"].unitsPerEm
        checked = []
        for record in recipe["objects"]:
            if record["kind"] != "raw_text":
                continue
            text = next(f["after"] for f in record["fields"] if f["type"] == "string")
            missing = sorted({c for c in text if not c.isspace() and ord(c) not in cmap})
            if missing:
                raise UiPatchError(f"Korean UI glyphs missing: {missing}")
            widths = [sum(metrics[cmap[ord(c)]][0] for c in line) / units * record["font_size"] for line in text.split("\n")]
            if max(widths) > record["rect_width"]:
                raise UiPatchError(f"raw UI text exceeds its width: {record['path_id']}")
            checked.append({"path_id": record["path_id"], "line_widths": widths, "rect_width": record["rect_width"], "missing_glyphs": []})
    finally:
        parsed.close()
    return {"path_id": spec["path_id"], "sha256": spec["sha256"], "checks": checked, "runtime_verified": False}


def patch_ui(environment: Any, recipe: dict[str, Any]) -> dict[str, Any]:
    rows = recipe["objects"]
    ids = [r["path_id"] for r in rows]
    if len(ids) != len(set(ids)) or not ids:
        raise UiPatchError("duplicate or missing UI repair targets")
    font = validate_font(environment, recipe)
    pending, records = [], []
    # Preflight every target before mutating any object.
    for record in rows:
        pid = record["path_id"]
        obj = environment.file.objects[pid]
        if obj.type.name != "MonoBehaviour":
            raise UiPatchError(f"UI repair target is not MonoBehaviour: {pid}")
        source, expected = expected_payload(record)
        effective = bytes(obj.data) if obj.data is not None else obj.get_raw_data()
        if effective not in (source, expected):
            raise UiPatchError(f"unknown UI object version: {pid}: {digest(effective)}")
        if effective != expected:
            pending.append((obj, expected))
        records.append({"path_id": pid, "kind": record["kind"], "source_sha256": digest(source), "patched_sha256": digest(expected)})
    for obj, expected in pending:
        obj.set_raw_data(expected)
    return {
        "changed_path_ids": sorted(obj.path_id for obj, _ in pending),
        "target_path_ids": sorted(ids),
        "objects": records,
        "raw_font": font,
        "runtime_verified": False,
    }
