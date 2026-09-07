"""Guarded raw-object patch for the serialized HUDHHint UnityEngine.UI.Text."""

from __future__ import annotations

from io import BytesIO
import hashlib
import struct
from typing import Any

from fontTools.ttLib import TTFont


TEXT_PATH_ID = 324693
GAMEOBJECT_PATH_ID = 54574
SCRIPT_FILE_ID = 1
SCRIPT_PATH_ID = 1018
SOURCE_FONT_PATH_ID = 9107
TARGET_FONT_PATH_ID = 9108
ORIGINAL_OBJECT_SHA256 = "9e31d146b9ade707e7ce8f34243cca4d1dc9c071769e6e3b08f7af5ba73a2f0e"
ORIGINAL_OBJECT_BYTES = bytes.fromhex(
    "000000002ed50000000000000100000001000000fa030000000000000000000000"
    "0000000000000000000000c1782b3fc1782b3fc1782b3f0000803f0100000001"
    "0000000000000000000000932300000000000012000000000000000000000001"
    "0000002800000003000000000000000100000001000000010000000000803f33"
    "000000e698bee7a4bae68898e69697485544205b485d2020202020e59cb0e59bbe"
    "e4bca0e98081205b4d5d202020202020202020202000"
)
SOURCE_FONT_SHA256 = "78101f93fb76951da79728a7ece95a974fb8f95f2024fa50792bc79627951a07"
TARGET_FONT_SHA256 = "061c9a55b6b731af2a4667f06efb82ed7c80ba99d5350c1bba76f80472ea304f"
SOURCE_TEXT = "显示战斗HUD [H]     地图传送 [M]           "
FONT_POINTER_OFFSET = 72
FONT_SIZE_OFFSET = 84
TEXT_LENGTH_OFFSET = 128
TEXT_PAYLOAD_OFFSET = 132
RECT_WIDTH = 561.0524291992188
FONT_SIZE = 18


class HudPatchError(RuntimeError):
    """Raised when the fixed object, replacement text, or embedded font is invalid."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _effective_bytes(obj: Any) -> bytes:
    return bytes(obj.data) if obj.data is not None else obj.get_raw_data()


def _align4(length: int) -> int:
    return (length + 3) & ~3


def _validate_ko(ko: str) -> bytes:
    if not isinstance(ko, str) or not ko:
        raise HudPatchError("ko must be a nonempty string")
    if "\n" in ko or "\r" in ko:
        raise HudPatchError("HUD hint must remain a single line")
    if ko.count("[H]") != 1 or ko.count("[M]") != 1 or ko.index("[H]") > ko.index("[M]"):
        raise HudPatchError("HUD hint must preserve ordered [H] and [M] tokens")
    if " [H]     " not in ko or not ko.endswith(" [M]           "):
        raise HudPatchError("HUD hint must preserve fixed token spacing and trailing padding")
    return ko.encode("utf-8")


def _validate_original(raw: bytes) -> None:
    if len(raw) != 184:
        raise HudPatchError("HUD Text original object size mismatch")
    if struct.unpack_from("<iq", raw, 0) != (0, GAMEOBJECT_PATH_ID):
        raise HudPatchError("HUD Text GameObject pointer mismatch")
    if struct.unpack_from("<iq", raw, 16) != (SCRIPT_FILE_ID, SCRIPT_PATH_ID):
        raise HudPatchError("HUD Text script pointer mismatch")
    if struct.unpack_from("<iq", raw, FONT_POINTER_OFFSET) != (0, SOURCE_FONT_PATH_ID):
        raise HudPatchError("HUD Text source font pointer mismatch")
    if struct.unpack_from("<i", raw, FONT_SIZE_OFFSET)[0] != FONT_SIZE:
        raise HudPatchError("HUD Text font size mismatch")
    length = struct.unpack_from("<i", raw, TEXT_LENGTH_OFFSET)[0]
    source = SOURCE_TEXT.encode("utf-8")
    if length != len(source) or raw[TEXT_PAYLOAD_OFFSET : TEXT_PAYLOAD_OFFSET + length] != source:
        raise HudPatchError("HUD Text source literal mismatch")
    aligned_end = TEXT_PAYLOAD_OFFSET + _align4(length)
    if raw[TEXT_PAYLOAD_OFFSET + length : aligned_end] != b"\0":
        raise HudPatchError("HUD Text original alignment padding mismatch")


def _font_record(serialized: Any, path_id: int, expected_name: str, expected_hash: str, text: str) -> dict[str, Any]:
    try:
        obj = serialized.objects[path_id]
    except KeyError as exc:
        raise HudPatchError(f"embedded Font {path_id} is missing") from exc
    if obj.type.name != "Font":
        raise HudPatchError(f"PathID {path_id} is not Font")
    font = obj.read()
    data = bytes(font.m_FontData)
    digest = _sha256(data)
    if font.m_Name != expected_name or digest != expected_hash:
        raise HudPatchError(f"embedded Font {path_id} identity/hash mismatch")
    parsed = TTFont(BytesIO(data), lazy=False)
    try:
        cmap = parsed.getBestCmap() or {}
        missing = sorted({character for character in text if not character.isspace() and ord(character) not in cmap})
        units_per_em = int(parsed["head"].unitsPerEm)
        hmtx = parsed["hmtx"].metrics
        notdef_advance = hmtx.get(".notdef", (0, 0))[0]
        units = 0
        for character in text:
            glyph = cmap.get(ord(character))
            units += hmtx.get(glyph, (notdef_advance, 0))[0] if glyph else notdef_advance
        width = units / units_per_em * FONT_SIZE
    finally:
        parsed.close()
    return {
        "path_id": path_id,
        "name": font.m_Name,
        "font_data_sha256": digest,
        "units_per_em": units_per_em,
        "missing_glyphs": missing,
        "estimated_width_at_18px": width,
    }


def _patched_bytes(original: bytes, ko_bytes: bytes) -> bytes:
    old_length = len(SOURCE_TEXT.encode("utf-8"))
    old_end = TEXT_PAYLOAD_OFFSET + _align4(old_length)
    padding = b"\0" * (_align4(len(ko_bytes)) - len(ko_bytes))
    patched = bytearray(original[:TEXT_LENGTH_OFFSET])
    patched.extend(struct.pack("<i", len(ko_bytes)))
    patched.extend(ko_bytes)
    patched.extend(padding)
    patched.extend(original[old_end:])
    struct.pack_into("<iq", patched, FONT_POINTER_OFFSET, 0, TARGET_FONT_PATH_ID)
    return bytes(patched)


def _validate_patched(raw: bytes, ko_bytes: bytes) -> None:
    if struct.unpack_from("<iq", raw, 0) != (0, GAMEOBJECT_PATH_ID):
        raise HudPatchError("patched GameObject pointer changed")
    if struct.unpack_from("<iq", raw, 16) != (SCRIPT_FILE_ID, SCRIPT_PATH_ID):
        raise HudPatchError("patched script pointer changed")
    if struct.unpack_from("<iq", raw, FONT_POINTER_OFFSET) != (0, TARGET_FONT_PATH_ID):
        raise HudPatchError("patched destination font pointer mismatch")
    if struct.unpack_from("<i", raw, TEXT_LENGTH_OFFSET)[0] != len(ko_bytes):
        raise HudPatchError("patched UTF-8 byte length mismatch")
    if raw[TEXT_PAYLOAD_OFFSET : TEXT_PAYLOAD_OFFSET + len(ko_bytes)] != ko_bytes:
        raise HudPatchError("patched UTF-8 payload mismatch")
    end = TEXT_PAYLOAD_OFFSET + _align4(len(ko_bytes))
    if any(raw[TEXT_PAYLOAD_OFFSET + len(ko_bytes) : end]):
        raise HudPatchError("patched alignment padding is not zero")
    if len(raw) % 4:
        raise HudPatchError("patched object size is not four-byte aligned")


def patch_hud(environment: Any, ko: str) -> dict[str, Any]:
    """Patch only Text PathID 324693 and return changed IDs plus validation."""
    ko_bytes = _validate_ko(ko)
    serialized = environment.file
    try:
        obj = serialized.objects[TEXT_PATH_ID]
    except KeyError as exc:
        raise HudPatchError("HUD Text PathID 324693 is missing") from exc
    if obj.type.name != "MonoBehaviour":
        raise HudPatchError("HUD Text PathID 324693 is not MonoBehaviour")

    if _sha256(ORIGINAL_OBJECT_BYTES) != ORIGINAL_OBJECT_SHA256:
        raise HudPatchError("embedded original HUD object guard is internally inconsistent")
    _validate_original(ORIGINAL_OBJECT_BYTES)
    expected = _patched_bytes(ORIGINAL_OBJECT_BYTES, ko_bytes)
    expected_hash = _sha256(expected)
    effective = _effective_bytes(obj)
    effective_hash = _sha256(effective)

    source_font = _font_record(serialized, SOURCE_FONT_PATH_ID, "SourceHanSerifCN-Heavy-4", SOURCE_FONT_SHA256, ko)
    target_font = _font_record(serialized, TARGET_FONT_PATH_ID, "SourceHanSansKR-Regular", TARGET_FONT_SHA256, ko)
    if not source_font["missing_glyphs"]:
        raise HudPatchError("source CN font unexpectedly covers every Korean glyph")
    if target_font["missing_glyphs"]:
        raise HudPatchError(f"destination KR font is missing glyphs: {target_font['missing_glyphs']}")
    if target_font["estimated_width_at_18px"] > RECT_WIDTH:
        raise HudPatchError("Korean HUD hint exceeds the fixed RectTransform width")

    validation = {
        "object_path_id": TEXT_PATH_ID,
        "gameobject_path_id": GAMEOBJECT_PATH_ID,
        "script_pointer": [SCRIPT_FILE_ID, SCRIPT_PATH_ID],
        "source_object_sha256": ORIGINAL_OBJECT_SHA256,
        "patched_object_sha256": expected_hash,
        "source_object_size": len(ORIGINAL_OBJECT_BYTES),
        "patched_object_size": len(expected),
        "text_utf8_bytes": len(ko_bytes),
        "alignment": 4,
        "font_pointer_before": [0, SOURCE_FONT_PATH_ID],
        "font_pointer_after": [0, TARGET_FONT_PATH_ID],
        "source_font": source_font,
        "target_font": target_font,
        "rect_width": RECT_WIDTH,
        "width_fits": True,
        "runtime_verified": False,
    }
    if effective_hash == expected_hash:
        _validate_patched(effective, ko_bytes)
        validation["idempotent"] = True
        return {"changed_path_ids": [], "validation": validation}
    if effective_hash != ORIGINAL_OBJECT_SHA256 or effective != ORIGINAL_OBJECT_BYTES:
        raise HudPatchError(f"HUD Text object is an unknown version: {effective_hash}")

    changed_before = {item.path_id for item in environment.objects if item.data is not None}
    obj.set_raw_data(expected)
    _validate_patched(_effective_bytes(obj), ko_bytes)
    changed_after = {item.path_id for item in environment.objects if item.data is not None}
    if changed_after - changed_before != {TEXT_PATH_ID}:
        raise HudPatchError("HUD adapter changed an unexpected object")
    validation["idempotent"] = False
    return {"changed_path_ids": [TEXT_PATH_ID], "validation": validation}
