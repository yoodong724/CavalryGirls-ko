"""Guarded full-rectangle Sprite patch for Title_Japanese (PathID 14470)."""

from __future__ import annotations

import copy
import hashlib
import struct
from typing import Any

from UnityPy.helpers.MeshHelper import MeshHandler


SPRITE_PATH_ID = 14470
TEXTURE_PATH_ID = 5467
QUAD_DONOR_PATH_ID = 9320
ORIGINAL_OBJECT_SHA256 = "5248bfc3b0aad6ff83573fe9c8ef09b862068b95b4d6cc17e5ee0de99e2a53c8"
PATCHED_OBJECT_SHA256 = "83f68781cba0c8c079e80158a472f819d0a219737e6b9e565c552a4e26032ee1"
EXPECTED_RECT = (0.0, 0.0, 640.0, 360.0)
EXPECTED_PIVOT = (0.5, 0.5)
EXPECTED_PPU = 100.0
EXPECTED_UV_TRANSFORM = (100.0, 320.0, 100.0, 180.0)


class TitleSpriteError(RuntimeError):
    """Raised when the fixed Sprite or its full-rect donor violates the contract."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _rect(value: Any) -> tuple[float, float, float, float]:
    return (float(value.x), float(value.y), float(value.width), float(value.height))


def _vec2(value: Any) -> tuple[float, float]:
    return (float(value.x), float(value.y))


def _vec4(value: Any) -> tuple[float, float, float, float]:
    return (float(value.x), float(value.y), float(value.z), float(value.w))


def _effective_bytes(obj: Any) -> bytes:
    return bytes(obj.data) if obj.data is not None else obj.get_raw_data()


def _validate_outer(sprite: Any) -> None:
    rd = sprite.m_RD
    if sprite.m_Name != "Title_Japanese":
        raise TitleSpriteError("Sprite 14470 name mismatch")
    if rd.texture.m_FileID != 0 or rd.texture.m_PathID != TEXTURE_PATH_ID:
        raise TitleSpriteError("Sprite 14470 Texture2D reference mismatch")
    if _rect(sprite.m_Rect) != EXPECTED_RECT:
        raise TitleSpriteError("Sprite 14470 outer rect mismatch")
    if _vec2(sprite.m_Pivot) != EXPECTED_PIVOT:
        raise TitleSpriteError("Sprite 14470 pivot mismatch")
    if float(sprite.m_PixelsToUnits) != EXPECTED_PPU:
        raise TitleSpriteError("Sprite 14470 pixels-to-units mismatch")
    if _vec4(rd.uvTransform) != EXPECTED_UV_TRANSFORM:
        raise TitleSpriteError("Sprite 14470 UV transform mismatch")
    if _vec2(sprite.m_Offset) != (0.0, 0.0) or _vec4(sprite.m_Border) != (0.0, 0.0, 0.0, 0.0):
        raise TitleSpriteError("Sprite 14470 offset/border mismatch")
    if rd.settingsRaw != 64:
        raise TitleSpriteError("Sprite 14470 render settings mismatch")
    if rd.alphaTexture.m_PathID != 0 or sprite.m_SpriteAtlas.m_PathID != 0:
        raise TitleSpriteError("Sprite 14470 unexpectedly uses alpha texture or atlas")


def _validate_texture(serialized: Any) -> None:
    try:
        obj = serialized.objects[TEXTURE_PATH_ID]
    except KeyError as exc:
        raise TitleSpriteError("Texture2D 5467 is missing") from exc
    if obj.type.name != "Texture2D":
        raise TitleSpriteError("PathID 5467 is not Texture2D")
    texture = obj.read()
    if (
        texture.m_Name != "Title_Japanese"
        or texture.m_Width != 640
        or texture.m_Height != 360
        or int(texture.m_TextureFormat) != 12
        or texture.m_StreamData.offset != 2_307_466_956
        or texture.m_StreamData.size != 230_400
    ):
        raise TitleSpriteError("Texture2D 5467 metadata mismatch")


def _validate_donor(donor: Any, target: Any) -> None:
    rd = donor.m_RD
    vertex = rd.m_VertexData
    target_channels = [
        (item.stream, item.offset, item.format, item.dimension)
        for item in target.m_RD.m_VertexData.m_Channels
    ]
    donor_channels = [
        (item.stream, item.offset, item.format, item.dimension)
        for item in vertex.m_Channels
    ]
    if (
        donor.m_Name != "24_10_8_Chinese"
        or vertex.m_VertexCount != 4
        or len(vertex.m_DataSize) != 208
        or list(struct.unpack("<6H", bytes(rd.m_IndexBuffer))) != [3, 0, 1, 2, 1, 0]
        or len(rd.m_SubMeshes) != 1
        or rd.m_SubMeshes[0].indexCount != 6
        or rd.m_SubMeshes[0].vertexCount != 4
        or donor_channels != target_channels
    ):
        raise TitleSpriteError("full-rectangle quad donor 9320 metadata mismatch")


def _quad_vertex_data(donor_data: bytes) -> bytes:
    positions = (
        (-3.2, 1.8, 0.0),
        (3.2, -1.8, 0.0),
        (3.2, 1.8, 0.0),
        (-3.2, -1.8, 0.0),
    )
    prefix = b"".join(struct.pack("<3f", *position) for position in positions)
    return prefix + donor_data[48:]


def _is_full_quad(sprite: Any) -> bool:
    rd = sprite.m_RD
    vertex = rd.m_VertexData
    if (
        _rect(rd.textureRect) != EXPECTED_RECT
        or _vec2(rd.textureRectOffset) != (0.0, 0.0)
        or vertex.m_VertexCount != 4
        or len(vertex.m_DataSize) != 208
        or len(rd.m_SubMeshes) != 1
        or rd.m_SubMeshes[0].indexCount != 6
        or rd.m_SubMeshes[0].vertexCount != 4
    ):
        return False
    try:
        if list(struct.unpack("<6H", bytes(rd.m_IndexBuffer))) != [3, 0, 1, 2, 1, 0]:
            return False
        positions = struct.unpack("<12f", bytes(vertex.m_DataSize[:48]))
    except (struct.error, TypeError):
        return False
    expected = (-3.2, 1.8, 0.0, 3.2, -1.8, 0.0, 3.2, 1.8, 0.0, -3.2, -1.8, 0.0)
    return all(abs(left - right) < 1e-6 for left, right in zip(positions, expected))


def _validate_full_quad(sprite: Any, version: Any) -> None:
    if not _is_full_quad(sprite):
        raise TitleSpriteError("patched Sprite is not the fixed full-rectangle quad")
    mesh = MeshHandler(sprite.m_RD, version=version)
    mesh.process()
    expected_vertices = [
        (-3.2, 1.8, 0.0),
        (3.2, -1.8, 0.0),
        (3.2, 1.8, 0.0),
        (-3.2, -1.8, 0.0),
    ]
    if any(
        any(abs(left - right) >= 1e-6 for left, right in zip(observed, expected))
        for observed, expected in zip(mesh.m_Vertices or [], expected_vertices)
    ) or len(mesh.m_Vertices or []) != 4:
        raise TitleSpriteError("patched quad vertex positions mismatch")
    if mesh.m_UV0 != [(0.0, 0.0)] * 4:
        raise TitleSpriteError("patched quad zero-UV stream convention mismatch")
    if mesh.get_triangles() != [[(3, 0, 1), (2, 1, 0)]]:
        raise TitleSpriteError("patched quad triangle topology mismatch")
    bounds = sprite.m_RD.m_SubMeshes[0].localAABB
    center = (bounds.m_Center.x, bounds.m_Center.y, bounds.m_Center.z)
    extent = (bounds.m_Extent.x, bounds.m_Extent.y, bounds.m_Extent.z)
    if center != (0.0, 0.0, 0.0) or extent != (0.0, 0.0, 0.0):
        raise TitleSpriteError("quad donor localAABB convention changed")


def _validation(before_hash: str, after_hash: str, *, idempotent: bool) -> dict[str, Any]:
    return {
        "sprite_path_id": SPRITE_PATH_ID,
        "texture_path_id": TEXTURE_PATH_ID,
        "before_object_sha256": before_hash,
        "after_object_sha256": after_hash,
        "idempotent": idempotent,
        "outer_rect_preserved": True,
        "pivot_preserved": True,
        "pixels_to_units_preserved": True,
        "texture_reference_preserved": True,
        "uv_transform_preserved": True,
        "texture_rect": list(EXPECTED_RECT),
        "texture_rect_offset": [0.0, 0.0],
        "vertex_count": 4,
        "triangle_count": 2,
        "quad_positions": [[-3.2, 1.8], [3.2, -1.8], [3.2, 1.8], [-3.2, -1.8]],
        "uv_stream": "zero-valued, with preserved uvTransform mapping positions to full texture",
        "submesh_local_aabb": {"center": [0.0, 0.0, 0.0], "extent": [0.0, 0.0, 0.0]},
        "full_texture_coverage": True,
        "runtime_verified": False,
    }


def patch_title_sprite(environment: Any) -> dict[str, Any]:
    """Patch only Sprite 14470 and return changed IDs plus validation metadata."""
    serialized = environment.file
    try:
        obj = serialized.objects[SPRITE_PATH_ID]
    except KeyError as exc:
        raise TitleSpriteError("Sprite 14470 is missing") from exc
    if obj.type.name != "Sprite":
        raise TitleSpriteError("PathID 14470 is not Sprite")

    effective = _effective_bytes(obj)
    effective_hash = _sha256(effective)
    if PATCHED_OBJECT_SHA256 and effective_hash == PATCHED_OBJECT_SHA256:
        if obj.data is None:
            sprite = obj.read()
            _validate_outer(sprite)
            _validate_full_quad(sprite, serialized.version)
        _validate_texture(serialized)
        return {
            "changed_path_ids": [],
            "validation": _validation(effective_hash, effective_hash, idempotent=True),
        }
    if effective_hash != ORIGINAL_OBJECT_SHA256:
        raise TitleSpriteError(f"Sprite 14470 object hash mismatch: {effective_hash}")

    changed_before = {item.path_id for item in environment.objects if item.data is not None}
    sprite = obj.read()
    _validate_outer(sprite)
    _validate_texture(serialized)
    if _is_full_quad(sprite):
        raise TitleSpriteError("original hash unexpectedly contains the patched full quad")

    try:
        donor_obj = serialized.objects[QUAD_DONOR_PATH_ID]
    except KeyError as exc:
        raise TitleSpriteError("quad donor Sprite 9320 is missing") from exc
    if donor_obj.type.name != "Sprite":
        raise TitleSpriteError("quad donor PathID 9320 is not Sprite")
    donor = donor_obj.read()
    _validate_donor(donor, sprite)

    rd = sprite.m_RD
    rd.textureRect.x = 0.0
    rd.textureRect.y = 0.0
    rd.textureRect.width = 640.0
    rd.textureRect.height = 360.0
    rd.textureRectOffset.x = 0.0
    rd.textureRectOffset.y = 0.0
    rd.m_IndexBuffer = copy.deepcopy(donor.m_RD.m_IndexBuffer)
    rd.m_SubMeshes = copy.deepcopy(donor.m_RD.m_SubMeshes)
    rd.m_VertexData = copy.deepcopy(donor.m_RD.m_VertexData)
    rd.m_VertexData.m_DataSize = _quad_vertex_data(bytes(rd.m_VertexData.m_DataSize))
    _validate_outer(sprite)
    _validate_full_quad(sprite, serialized.version)
    sprite.save()

    after = _effective_bytes(obj)
    after_hash = _sha256(after)
    if PATCHED_OBJECT_SHA256 and after_hash != PATCHED_OBJECT_SHA256:
        raise TitleSpriteError(f"patched Sprite object hash mismatch: {after_hash}")
    changed_after = {item.path_id for item in environment.objects if item.data is not None}
    if changed_after - changed_before != {SPRITE_PATH_ID}:
        raise TitleSpriteError("patch changed an unexpected object")
    return {
        "changed_path_ids": [SPRITE_PATH_ID],
        "validation": _validation(effective_hash, after_hash, idempotent=False),
    }
