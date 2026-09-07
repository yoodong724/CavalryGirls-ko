"""In-memory TMP fallback patch for Cavalry Girls.

The caller owns loading and saving ``resources.assets``.  This module only
changes ObjectReader buffers in the supplied UnityPy environment; it never
writes an asset file or bundle.
"""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from os import PathLike
from typing import Any, Iterable

import UnityPy
from UnityPy.helpers import TypeTreeHelper
from UnityPy.streams import EndianBinaryReader, EndianBinaryWriter


TMP_FONT_TYPE_HASH = "300fd99066f8c288e85f5e81ce65fe34"
RESOURCE_FILE_NAME = "resources.assets"
KOREAN_FONT_PATH_ID = 308666
KOREAN_FONT_NAME = "SourceHanSansKR"

TARGETS = {
    308665: (
        "Main SDF",
        "dominant TextMeshProUGUI font (61 observed local PPtr references)",
    ),
    308667: (
        "RuiZiZhenYanTiMianFeiShangYong-2 SDF",
        "secondary TextMeshProUGUI font (23 observed local PPtr references)",
    ),
    308668: (
        "ZiTiChuanQiTeZhanTi-MianFeiShangYong-2 SDF",
        "TextMeshProUGUI font (1 observed local PPtr reference)",
    ),
    308669: (
        "素材集市社会体 SDF",
        "TextMeshPro font (12 observed local PPtr references)",
    ),
    308670: (
        "腾祥细潮黑简 SDF",
        "TextMeshProUGUI font (1 observed local PPtr reference)",
    ),
}


class FontPatchError(RuntimeError):
    """Raised when the supplied assets do not satisfy the fixed PoC contract."""


def _digest(data: bytes) -> str:
    return sha256(data).hexdigest()


def _type_hash(obj: Any) -> str | None:
    serialized_type = getattr(obj, "serialized_type", None)
    value = getattr(serialized_type, "old_type_hash", None)
    return value.hex() if value else None


def _serialize_typetree(obj: Any, tree: dict[str, Any], node: Any) -> bytes:
    """Serialize without calling ObjectReader.set_raw_data()."""
    writer = EndianBinaryWriter(endian=obj.reader.endian)
    TypeTreeHelper.write_typetree(tree, node, writer, obj.assets_file)
    return writer.bytes


def _active_data(obj: Any) -> bytes:
    """Return pending replacement bytes when UnityPy has one."""
    data = getattr(obj, "data", None)
    return data if data is not None else obj.get_raw_data()


def _deserialize_typetree(obj: Any, data: bytes, node: Any) -> dict[str, Any]:
    """Parse explicit bytes, including an unsaved ObjectReader.data buffer."""
    reader = EndianBinaryReader(data, endian=obj.reader.endian)
    return TypeTreeHelper.read_typetree(
        node,
        reader,
        as_dict=True,
        assetsfile=obj.assets_file,
        byte_size=len(data),
        check_read=True,
    )


def _load_font_node(font_bundle_path: str | PathLike[str]) -> Any:
    # Loading is read-only. The returned Environment is intentionally not
    # exposed, so callers cannot accidentally treat it as the patch target.
    bundle_env = UnityPy.load(str(font_bundle_path))
    nodes = []
    for obj in bundle_env.objects:
        serialized_type = getattr(obj, "serialized_type", None)
        node = getattr(serialized_type, "node", None)
        if _type_hash(obj) == TMP_FONT_TYPE_HASH and node is not None:
            nodes.append(node)
    if not nodes:
        raise FontPatchError(
            "font bundle has no TMP FontAsset type tree with hash "
            f"{TMP_FONT_TYPE_HASH}"
        )
    return nodes[0]


def _resource_font_objects(env: Any) -> dict[int, Any]:
    objects: dict[int, Any] = {}
    for obj in env.objects:
        assets_file = getattr(obj, "assets_file", None)
        if getattr(assets_file, "name", None) != RESOURCE_FILE_NAME:
            continue
        if _type_hash(obj) != TMP_FONT_TYPE_HASH:
            continue
        objects[obj.path_id] = obj
    return objects


def _local_fallback_ids(tree: dict[str, Any]) -> list[int]:
    result = []
    for pointer in tree.get("m_FallbackFontAssetTable", []):
        if pointer.get("m_FileID") == 0 and pointer.get("m_PathID"):
            result.append(pointer["m_PathID"])
    return result


def _path_exists(graph: dict[int, Iterable[int]], start: int, goal: int) -> bool:
    pending = [start]
    visited: set[int] = set()
    while pending:
        current = pending.pop()
        if current == goal:
            return True
        if current in visited:
            continue
        visited.add(current)
        pending.extend(graph.get(current, ()))
    return False


def patch_fonts(env: Any, font_bundle_path: str | PathLike[str]) -> dict[str, Any]:
    """Attach the existing static Korean TMP atlas as a local fallback.

    Parameters
    ----------
    env:
        A UnityPy Environment already containing ``resources.assets``.
    font_bundle_path:
        Read-only path to ``fonts_assets_all.bundle``. Its embedded type tree
        is used to parse and serialize stripped TMP FontAsset objects.

    Returns
    -------
    dict
        A deterministic record of changed/unchanged PathIDs, byte hashes,
        reasons, and in-memory validation evidence.

    Raises
    ------
    FontPatchError
        If identities, coverage, exact no-op round-trips, PPtrs, or cycle
        checks fail. No asset file is saved by this function.
    """
    node = _load_font_node(font_bundle_path)
    objects = _resource_font_objects(env)
    required_ids = {KOREAN_FONT_PATH_ID, *TARGETS}
    missing = sorted(required_ids - objects.keys())
    if missing:
        raise FontPatchError(f"resources.assets is missing TMP FontAsset PathIDs: {missing}")

    # Read and serialize every FontAsset of this type in resources.assets.
    # This detects a mismatched borrowed type tree before any patch is applied.
    trees: dict[int, dict[str, Any]] = {}
    originals: dict[int, bytes] = {}
    roundtrip_ids: list[int] = []
    for path_id, obj in sorted(objects.items()):
        raw = _active_data(obj)
        tree = _deserialize_typetree(obj, raw, node)
        rebuilt = _serialize_typetree(obj, tree, node)
        if rebuilt != raw:
            raise FontPatchError(
                f"borrowed type tree is not an exact no-op round-trip for PathID {path_id}"
            )
        trees[path_id] = tree
        originals[path_id] = raw
        roundtrip_ids.append(path_id)

    expected_names = {KOREAN_FONT_PATH_ID: KOREAN_FONT_NAME}
    expected_names.update({path_id: value[0] for path_id, value in TARGETS.items()})
    for path_id, expected_name in expected_names.items():
        actual_name = trees[path_id].get("m_Name")
        if actual_name != expected_name:
            raise FontPatchError(
                f"PathID {path_id} identity mismatch: expected {expected_name!r}, "
                f"found {actual_name!r}"
            )

    korean_tree = trees[KOREAN_FONT_PATH_ID]
    if korean_tree.get("m_AtlasPopulationMode") != 0:
        raise FontPatchError("SourceHanSansKR is not a static TMP FontAsset")
    unicodes = {
        item.get("m_Unicode") for item in korean_tree.get("m_CharacterTable", [])
    }
    missing_hangul = [codepoint for codepoint in range(0xAC00, 0xD7A4) if codepoint not in unicodes]
    if missing_hangul:
        raise FontPatchError(
            f"SourceHanSansKR lacks {len(missing_hangul)} modern Hangul syllables"
        )

    graph = {
        path_id: _local_fallback_ids(tree) for path_id, tree in trees.items()
    }
    for path_id in TARGETS:
        if path_id == KOREAN_FONT_PATH_ID or _path_exists(
            graph, KOREAN_FONT_PATH_ID, path_id
        ):
            raise FontPatchError(
                f"adding {path_id} -> {KOREAN_FONT_PATH_ID} would create a fallback cycle"
            )

    proposed: dict[int, tuple[dict[str, Any], bytes]] = {}
    unchanged: list[dict[str, Any]] = []
    for path_id, (name, reason) in TARGETS.items():
        old_tree = trees[path_id]
        old_hash = _digest(originals[path_id])
        fallback_ids = _local_fallback_ids(old_tree)
        if KOREAN_FONT_PATH_ID in fallback_ids:
            unchanged.append(
                {
                    "path_id": path_id,
                    "name": name,
                    "sha256": old_hash,
                    "reason": "Korean fallback already present",
                }
            )
            continue
        new_tree = deepcopy(old_tree)
        new_tree["m_FallbackFontAssetTable"].append(
            {"m_FileID": 0, "m_PathID": KOREAN_FONT_PATH_ID}
        )
        new_data = _serialize_typetree(objects[path_id], new_tree, node)
        proposed[path_id] = (new_tree, new_data)

    changed: list[dict[str, Any]] = []
    try:
        for path_id, (new_tree, new_data) in proposed.items():
            obj = objects[path_id]
            before = originals[path_id]
            obj.set_raw_data(new_data)
            saved_tree = _deserialize_typetree(obj, new_data, node)
            pointers = saved_tree.get("m_FallbackFontAssetTable", [])
            if not any(
                pointer.get("m_FileID") == 0
                and pointer.get("m_PathID") == KOREAN_FONT_PATH_ID
                for pointer in pointers
            ):
                raise FontPatchError(f"serialized fallback PPtr verification failed for {path_id}")
            if objects[KOREAN_FONT_PATH_ID].assets_file is not obj.assets_file:
                raise FontPatchError(f"fallback PPtr for {path_id} is not file-local")
            if obj.assets_file.objects.get(KOREAN_FONT_PATH_ID) is not objects[KOREAN_FONT_PATH_ID]:
                raise FontPatchError(f"fallback PPtr for {path_id} does not resolve to SourceHanSansKR")
            changed.append(
                {
                    "path_id": path_id,
                    "name": TARGETS[path_id][0],
                    "before_sha256": _digest(before),
                    "after_sha256": _digest(new_data),
                    "reason": TARGETS[path_id][1],
                }
            )
    except Exception:
        for path_id in proposed:
            objects[path_id].set_raw_data(originals[path_id])
        raise

    return {
        "font_type_hash": TMP_FONT_TYPE_HASH,
        "fallback": {
            "m_FileID": 0,
            "m_PathID": KOREAN_FONT_PATH_ID,
            "name": KOREAN_FONT_NAME,
        },
        "changed": changed,
        "changed_path_ids": [item["path_id"] for item in changed],
        "unchanged": unchanged,
        "roundtrip_verified_path_ids": roundtrip_ids,
        "validation": {
            "hangul_syllables": 11172,
            "fallback_cycle_free": True,
            "local_pptr_resolved": True,
            "asset_files_saved": False,
            "runtime_verified": False,
        },
    }


__all__ = ["FontPatchError", "patch_fonts"]
