"""Exact-object UI repairs for the screenshot maintenance candidate.

The versioned recipe contains extracted source bytes and reviewed field edits.
Only those byte spans may change; unknown objects and overlapping edits fail.
"""

from __future__ import annotations

import hashlib
from io import BytesIO
import math
import re
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


_SIZE_TAG = re.compile(r"<size=([1-9][0-9]*)>|</size>")


def _visible_text_runs(text: str, base_size: int) -> list[list[tuple[str, int]]]:
    """Split UI Text into visible runs without interpreting escape characters.

    The maintenance recipes need only Unity's decimal ``size`` tag.  Rejecting
    every other tag keeps glyph and width validation fail-closed instead of
    accidentally measuring markup as rendered text.
    """
    if not isinstance(base_size, int) or isinstance(base_size, bool) or base_size <= 0:
        raise UiPatchError("invalid raw UI font_size")
    lines: list[list[tuple[str, int]]] = [[]]
    active_size = base_size
    size_open = False
    position = 0
    while position < len(text):
        if text[position] == "\n":
            lines.append([])
            position += 1
            continue
        if text[position] == "<":
            match = _SIZE_TAG.match(text, position)
            if match is None:
                raise UiPatchError("unsupported or malformed raw UI rich-text tag")
            if match.group(1) is not None:
                if size_open:
                    raise UiPatchError("nested raw UI size tags are unsupported")
                active_size = int(match.group(1))
                size_open = True
            else:
                if not size_open:
                    raise UiPatchError("unmatched raw UI closing size tag")
                active_size = base_size
                size_open = False
            position = match.end()
            continue
        end = position + 1
        while end < len(text) and text[end] not in "<\n":
            end += 1
        lines[-1].append((text[position:end], active_size))
        position = end
    if size_open:
        raise UiPatchError("unclosed raw UI size tag")
    return lines


def _measure_visible_text(
    text: str,
    base_size: int,
    cmap: dict[int, str],
    metrics: dict[str, tuple[int, int]],
    units: int,
) -> tuple[list[float], list[str]]:
    if not isinstance(units, int) or units <= 0:
        raise UiPatchError("invalid raw UI font units")
    lines = _visible_text_runs(text, base_size)
    missing = sorted({
        character
        for line in lines
        for run, _ in line
        for character in run
        if not character.isspace() and ord(character) not in cmap
    })
    if missing:
        return [], missing
    widths = [
        sum(
            metrics[cmap[ord(character)]][0] / units * size
            for run, size in line
            for character in run
        )
        for line in lines
    ]
    return widths, []


def _deferred_width_result(
    environment: Any,
    recipe: dict[str, Any],
    record: dict[str, Any],
    widths: list[float],
) -> dict[str, Any]:
    policy = record["width_check"]
    required_policy = {"mode", "reason", "debt_id", "nominal_required_width", "evidence"}
    if set(policy) != required_policy or policy["mode"] != "deferred_dynamic_layout":
        raise UiPatchError("invalid deferred raw UI width policy")
    if recipe.get("release_scope") != "stage8_local_candidate" or recipe.get("runtime_verified") is not False:
        raise UiPatchError("deferred raw UI width is limited to an unverified stage8 local candidate")
    if not isinstance(policy["reason"], str) or not policy["reason"].strip():
        raise UiPatchError("deferred raw UI width requires a reason")
    if policy["debt_id"] != "ui700767_dynamic_width" or record.get("path_id") != 700767:
        raise UiPatchError("unknown deferred raw UI width debt")
    nominal = policy["nominal_required_width"]
    measured = max(widths, default=0.0)
    if not isinstance(nominal, (int, float)) or isinstance(nominal, bool) or not math.isfinite(nominal):
        raise UiPatchError("invalid deferred raw UI nominal width")
    if not math.isclose(float(nominal), measured, rel_tol=0.0, abs_tol=1e-6):
        raise UiPatchError("deferred raw UI nominal width does not match font metrics")

    evidence = policy["evidence"]
    evidence_keys = {
        "text_source_sha256", "rect_transform_path_id", "rect_transform_sha256",
        "anchor_min", "anchor_max", "size_delta",
    }
    if not isinstance(evidence, dict) or set(evidence) != evidence_keys:
        raise UiPatchError("invalid deferred raw UI layout evidence")
    if evidence["text_source_sha256"] != record.get("source_sha256"):
        raise UiPatchError("deferred raw UI evidence is bound to another text object")
    rect_id = evidence["rect_transform_path_id"]
    if not isinstance(rect_id, int) or isinstance(rect_id, bool):
        raise UiPatchError("invalid deferred raw UI RectTransform path ID")
    rect = environment.file.objects[rect_id]
    if rect.type.name != "RectTransform" or digest(rect.get_raw_data()) != evidence["rect_transform_sha256"]:
        raise UiPatchError("deferred raw UI RectTransform identity/hash mismatch")
    tree = rect.read_typetree()
    serialized = {
        "anchor_min": [tree["m_AnchorMin"][axis] for axis in ("x", "y")],
        "anchor_max": [tree["m_AnchorMax"][axis] for axis in ("x", "y")],
        "size_delta": [tree["m_SizeDelta"][axis] for axis in ("x", "y")],
    }
    if any(serialized[key] != evidence[key] for key in serialized):
        raise UiPatchError("deferred raw UI serialized layout evidence mismatch")
    if serialized != {"anchor_min": [0.0, 0.0], "anchor_max": [1.0, 1.0], "size_delta": [0.0, 0.0]}:
        raise UiPatchError("deferred raw UI target is not a full-stretch dynamic layout")
    return {
        "width_status": "unverified",
        "runtime_verified": False,
        "due_gate": "public_release",
        "debt_id": policy["debt_id"],
        "nominal_required_width": measured,
        "reason": policy["reason"],
        "layout_evidence": evidence,
    }


def _raw_font_spec(recipe: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    spec = record.get("raw_font", recipe["raw_font"])
    if not isinstance(spec, dict) or set(spec) != {"path_id", "name", "sha256"}:
        raise UiPatchError("invalid raw UI font specification")
    if not isinstance(spec["path_id"], int) or isinstance(spec["path_id"], bool):
        raise UiPatchError("invalid raw UI font path ID")
    pointers = [field for field in record["fields"] if field["type"] == "pptr"]
    if len(pointers) != 1 or pointers[0].get("after") != [0, spec["path_id"]]:
        raise UiPatchError(f"raw UI font pointer/recipe mismatch: {record['path_id']}")
    return spec


def validate_font(environment: Any, recipe: dict[str, Any]) -> dict[str, Any]:
    global_spec = recipe["raw_font"]
    if not isinstance(global_spec, dict) or set(global_spec) != {"path_id", "name", "sha256"}:
        raise UiPatchError("invalid global raw UI font specification")
    parsed_fonts: dict[int, tuple[dict[str, Any], Any, dict[int, str], dict[str, tuple[int, int]], int]] = {}

    def font_metrics(spec: dict[str, Any]):
        path_id = spec["path_id"]
        cached = parsed_fonts.get(path_id)
        if cached is not None:
            if cached[0] != spec:
                raise UiPatchError("conflicting raw UI font identity")
            return cached
        obj = environment.file.objects[path_id]
        if obj.type.name != "Font":
            raise UiPatchError("raw Korean font object has wrong type")
        font = obj.read()
        data = bytes(font.m_FontData)
        if font.m_Name != spec["name"] or digest(data) != spec["sha256"]:
            raise UiPatchError("raw Korean font identity/hash mismatch")
        parsed = TTFont(BytesIO(data))
        cached = (spec, parsed, parsed.getBestCmap() or {}, parsed["hmtx"].metrics, parsed["head"].unitsPerEm)
        parsed_fonts[path_id] = cached
        return cached

    try:
        checked = []
        for record in recipe["objects"]:
            if record["kind"] != "raw_text":
                continue
            spec = _raw_font_spec(recipe, record)
            _, _, cmap, metrics, units = font_metrics(spec)
            text = next(f["after"] for f in record["fields"] if f["type"] == "string")
            widths, missing = _measure_visible_text(text, record["font_size"], cmap, metrics, units)
            if missing:
                raise UiPatchError(f"Korean UI glyphs missing for {record['path_id']}: {missing}")
            has_rect = "rect_width" in record
            has_deferred = "width_check" in record
            if has_rect == has_deferred:
                raise UiPatchError("raw UI requires exactly one width check")
            if has_rect:
                rect_width = record["rect_width"]
                if not isinstance(rect_width, (int, float)) or isinstance(rect_width, bool) or not math.isfinite(rect_width) or rect_width <= 0:
                    raise UiPatchError("invalid raw UI rect_width")
                if max(widths, default=0.0) > rect_width:
                    raise UiPatchError(f"raw UI text exceeds its width: {record['path_id']}")
                width_result = {
                    "width_status": "verified_static",
                    "rect_width": rect_width,
                    "nominal_required_width": max(widths, default=0.0),
                }
            else:
                width_result = _deferred_width_result(environment, recipe, record, widths)
            checked.append({
                "path_id": record["path_id"],
                "line_widths": widths,
                "missing_glyphs": [],
                "font_path_id": spec["path_id"],
                "font_sha256": spec["sha256"],
                **width_result,
            })
    finally:
        for _, parsed, _, _, _ in parsed_fonts.values():
            parsed.close()
    return {
        "path_id": global_spec["path_id"],
        "sha256": global_spec["sha256"],
        "fonts": [
            {"path_id": spec["path_id"], "name": spec["name"], "sha256": spec["sha256"]}
            for spec, _, _, _, _ in sorted(parsed_fonts.values(), key=lambda item: item[0]["path_id"])
        ],
        "checks": checked,
        "runtime_verified": False,
    }


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
        if "gameobject_path_id" in record and struct.unpack_from("<q", source, 4)[0] != record["gameobject_path_id"]:
            raise UiPatchError(f"UI GameObject identity mismatch: {pid}")
        if "script_pointer" in record and struct.unpack_from("<iq", source, 16) != tuple(record["script_pointer"]):
            raise UiPatchError(f"UI script identity mismatch: {pid}")
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
