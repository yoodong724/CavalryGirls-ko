#!/usr/bin/env python3
"""Build the guarded r03 Korean assets from one 8,774-record snapshot."""

from __future__ import annotations

import argparse
import gc
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any, Iterable

import UnityPy


ROOT = Path(__file__).resolve().parents[2]
WORK_ROOT = ROOT / "work"
DEFAULT_TRANSLATIONS = ROOT / "localization/translations/release-approved.jsonl"
SOURCE_REVISION = "f1c8fc8f5e5f7470da9ca0ad5a90bca21ab4d75c71e65509b2ffaf75b579eb6a"
RESOURCE_REL = Path("CavalryGirls_Data/resources.assets")
RESS_REL = Path("CavalryGirls_Data/resources.assets.resS")
FONT_REL = Path("AssetBundles/fonts_assets_all.bundle")
SOURCE_HASHES = {
    RESOURCE_REL.as_posix(): "d275481b891eef752e5d4279c587b564db4c5ef5e0291cc80af82a089d793838",
    RESS_REL.as_posix(): "8d60f1b9828c759f6483fa1998def795c9ed84dd0cd653950e2ee12b2acc6026",
    FONT_REL.as_posix(): "8c278b63786f5477ed82229a52184e8fdae81b8aedbf4f0434a1843423da4146",
}
DEPENDENCIES = {
    ROOT / "adapters/cavalry_girls.py": "d78d038b53318338f925b84d0e30ffe4edd9736eba1d9756a281234250056b2d",
    ROOT / "adapters/font_adapter.py": "ea41e64e4341a11611d9f594420c6f27d09866bdb1ebdaa64bd36ff505edf613",
    ROOT / "adapters/title_sprite.py": "0126a4d09bed1a4c9ba67a409bd30bd39d4e236839d450b675497dfd5b33236d",
    ROOT / "adapters/hud_adapter.py": "a15d1c3fc8bd157b4b23bd0bffdcc6aa56967bb3dd6e1fcc5ce605590c8c2344",
    ROOT / "release/assets/title-ko.dxt5": "646b631b7bc1b4399f52fc673ee2f9c2f26c8d0600a47c8a8e096107ba303daf",
    ROOT / "release/reference/source-hashes.json": "3e24084381f21027d6d4f0b8fafdd81409d27be7e50eda2c0e422f2cb5caf4ea",
    ROOT / "localization/corpus/source-manifest.json": "8512697c7bef09ffb105bfba4d3e30cc32eadcaa0e364de400b659d78d8dc39f",
    ROOT / "localization/corpus/patch-map.jsonl": "584138ff84818b7fa9d114b84a39536f762fa2fdbd2b2fce7e15885f29456bc2",
    ROOT / "localization/corpus/segments.jsonl": "0b8936745d93474ea54b64bb6d49df0fd9593367142815290137641f7317b413",
    ROOT / "localization/corpus/ui-static-r03.jsonl": "cf155021bcdeaced77974143236da03284771d5f0163d614f7879bcaddc262aa",
    ROOT / "localization/corpus/ui-static-patch-map-r03.jsonl": "1c78f7cff857cdac233dfc60f6d95fc4b1254d822f0c760ef03ca1a0aaf699b0",
}
PATCH_MAP = ROOT / "localization/corpus/patch-map.jsonl"
SEGMENTS = ROOT / "localization/corpus/segments.jsonl"
UI_SOURCE = ROOT / "localization/corpus/ui-static-r03.jsonl"
UI_PATCH_MAP = ROOT / "localization/corpus/ui-static-patch-map-r03.jsonl"
SOURCE_MANIFEST = ROOT / "localization/corpus/source-manifest.json"
SOURCE_HASH_MANIFEST = ROOT / "release/reference/source-hashes.json"
TITLE = ROOT / "release/assets/title-ko.dxt5"
TITLE_OFFSET = 2_307_466_956
TITLE_ORIGINAL_SHA256 = "084b9505d893ce23862fb8f3c0ed3a2fe496d807817155beaa3937f54c879fb1"
TARGET_TEXT_PATH_IDS = frozenset({6326, 6427, 6402, 6329, 6351})
MULTILINGUAL_PATH_IDS = frozenset({6326, 6427, 6402})
HUD_ID = "cg:UnityUI:resources:324693:m_Text"
UI_REPAIR_RECIPE = ROOT / "release/reference/ui-r04.json"
# Fixed separately so the default r03 rebuild does not require this candidate.
UI_REPAIR_DEPENDENCIES: dict[Path, str] = {
    UI_REPAIR_RECIPE: "e34c37a742aaace0db9ec1590cc42c04f38563efdf16162d8daaf2e8a295391f",
    ROOT / "adapters/ui_maintenance.py": "1ac6b67705bdeb870777ee05967420236bb53bfe0c28e156dee23a4a13ab9ebe",
}

UI_R05_RECIPE = ROOT / "release/reference/ui-r05.json"
UI_R05_DEPENDENCIES = {
    UI_R05_RECIPE: "4b62db744834b7942bc399d46116e3bef3283096688e26e1dd40d5a46a0fb779",
    ROOT / "adapters/ui_maintenance.py": UI_REPAIR_DEPENDENCIES[ROOT / "adapters/ui_maintenance.py"],
}


class BuildError(RuntimeError):
    pass


def _profile_module() -> Any:
    return load_module("cg_build_profile", ROOT / "tools/maintenance/profile.py")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_rows(path: Path) -> list[dict[str, Any]]:
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            raise BuildError(f"blank JSONL record: {path}:{number}")
        value = json.loads(line)
        if not isinstance(value, dict):
            raise BuildError(f"record is not an object: {path}:{number}")
        rows.append(value)
    return rows


def snapshot_part_hashes(path: Path, base_ids: set[str], supplemental_id: str) -> tuple[str, str]:
    base_lines, supplemental_lines = [], []
    for raw in path.read_bytes().splitlines(keepends=True):
        row = json.loads(raw)
        if row.get("id") in base_ids:
            base_lines.append(raw)
        elif row.get("id") == supplemental_id:
            supplemental_lines.append(raw)
    if len(base_lines) != len(base_ids) or len(supplemental_lines) != 1:
        raise BuildError("cannot derive exact base/supplemental snapshot byte hashes")
    return sha256_bytes(b"".join(base_lines)), sha256_bytes(b"".join(supplemental_lines))


def unique_index(rows: Iterable[dict[str, Any]], label: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for number, row in enumerate(rows, 1):
        identifier = row.get("id")
        if not isinstance(identifier, str) or not identifier:
            raise BuildError(f"{label} record {number} has no id")
        if identifier in result:
            raise BuildError(f"duplicate {label} id: {identifier}")
        result[identifier] = row
    return result


def split_snapshot(
    rows: Iterable[dict[str, Any]], base_ids: set[str], supplemental_ids: set[str]
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    index = unique_index(rows, "translation")
    expected = base_ids | supplemental_ids
    if set(index) != expected:
        raise BuildError(
            f"translation id set mismatch: missing={len(expected-set(index))}, "
            f"unknown={len(set(index)-expected)}"
        )
    for identifier, row in index.items():
        if row.get("status") != "release_approved":
            raise BuildError(f"{identifier}: status must be release_approved")
    return (
        {identifier: index[identifier] for identifier in base_ids},
        {identifier: index[identifier] for identifier in supplemental_ids},
    )


def validate_snapshot(
    translations: list[dict[str, Any]],
    segments: list[dict[str, Any]],
    patches: list[dict[str, Any]],
    ui_sources: list[dict[str, Any]],
    ui_patches: list[dict[str, Any]],
    *,
    source_revision: str = SOURCE_REVISION,
    base_count: int = 8773,
    record_count: int = 8774,
    hud_id: str = HUD_ID,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any], dict[str, Any]]:
    segment_index = unique_index(segments, "segment")
    patch_index = unique_index(patches, "patch-map")
    ui_source_index = unique_index(ui_sources, "supplemental source")
    ui_patch_index = unique_index(ui_patches, "supplemental patch-map")
    if len(patch_index) != base_count or set(segment_index) != set(patch_index):
        raise BuildError(f"base corpus/patch-map must contain the same {base_count} ids")
    if set(ui_source_index) != {hud_id} or set(ui_patch_index) != {hud_id}:
        raise BuildError("supplemental corpus/patch-map must contain only the pinned HUD id")
    base, supplemental = split_snapshot(translations, set(patch_index), {hud_id})
    context_pairs: set[tuple[str, str]] = set()
    revisions: set[str] = set()
    for identifier, row in base.items():
        source = segment_index[identifier]
        source_info = source.get("source") or {}
        patch = patch_index[identifier]
        if (
            row.get("source_revision") != source_revision
            or source.get("source_revision") != source_revision
            or row.get("source_sha256") != source_info.get("text_sha256")
            or sha256_bytes(str(source_info.get("text", "")).encode()) != source_info.get("text_sha256")
            or not isinstance(row.get("ko"), str)
            or patch.get("target_text_sha256") != sha256_bytes(str(patch.get("target_text", "")).encode())
        ):
            raise BuildError(f"{identifier}: base source/translation/patch guard mismatch")
        context_id, digest = row.get("context_pack_id"), row.get("context_pack_digest")
        revision = row.get("translation_revision")
        if not context_id or not isinstance(digest, str) or len(digest) != 64 or not revision:
            raise BuildError(f"{identifier}: approval context metadata is incomplete")
        context_pairs.add((context_id, digest))
        revisions.add(revision)
    ui = supplemental[hud_id]
    ui_source = ui_source_index[hud_id]
    ui_info = ui_source.get("source") or {}
    ui_patch = ui_patch_index[hud_id]
    if (
        ui.get("source_revision") != source_revision
        or ui_source.get("source_revision") != source_revision
        or ui.get("source_sha256") != ui_info.get("text_sha256")
        or ui_patch.get("source_sha256") != ui.get("source_sha256")
        or sha256_bytes(str(ui_info.get("text", "")).encode()) != ui.get("source_sha256")
        or not isinstance(ui.get("ko"), str)
        or not ui.get("context_pack_id")
        or len(ui.get("context_pack_digest", "")) != 64
        or not ui.get("translation_revision")
    ):
        raise BuildError("supplemental HUD approval/source/patch guard mismatch")
    if base.get("cg:Descriptions:LanguageTxt:o0:Japanese", {}).get("ko") != "한국어":
        raise BuildError("self-language label must be 한국어")
    return base, ui, {
        "record_count": record_count,
        "base_record_count": base_count,
        "supplemental_record_count": 1,
        "context_packs": [
            {"context_pack_id": left, "context_pack_digest": right}
            for left, right in sorted(context_pairs)
        ],
        "translation_revisions": sorted(revisions),
    }


def validate_profile_snapshot(
    profile: Any,
    translations_path: Path,
    segments_path: Path,
    patches_path: Path,
    *,
    supplemental_source_path: Path | None = None,
    supplemental_patch_path: Path | None = None,
    static_source_path: Path | None = None,
    static_recipe: dict[str, Any] | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Validate an update snapshot using profile-pinned counts and revision."""
    profile_raw = profile.raw if hasattr(profile, "raw") else profile
    if not isinstance(profile_raw, dict):
        raise BuildError("profile snapshot input is not a mapping")
    source_revision = profile.source_revision
    translations = read_rows(translations_path)
    segments = read_rows(segments_path)
    patches = read_rows(patches_path)
    segment_index = unique_index(segments, "profile segment")
    patch_index = unique_index(patches, "profile patch-map")
    translation_index = unique_index(translations, "profile translation")
    static_sources = unique_index(read_rows(static_source_path), "profile static UI") if static_source_path else {}
    expected_count = profile.record_count
    if len(translation_index) != expected_count:
        raise BuildError(f"profile translation count mismatch: expected {expected_count}")
    supplemental_id = profile_raw.get("supplemental_id") or profile_raw.get("hud_id")
    if not isinstance(supplemental_id, str):
        supplemental_id = None
    if supplemental_id is None and supplemental_source_path and supplemental_patch_path:
        supplemental_rows = read_rows(supplemental_source_path)
        supplemental_patches = read_rows(supplemental_patch_path)
        supplemental_index = unique_index(supplemental_rows, "profile supplemental source")
        supplemental_patch_index = unique_index(supplemental_patches, "profile supplemental patch-map")
        if len(supplemental_index) != 1 or set(supplemental_index) != set(supplemental_patch_index):
            raise BuildError("profile supplemental source/patch-map mismatch")
        supplemental_id = next(iter(supplemental_index))
    if supplemental_id is None:
        supplemental_id = HUD_ID
    base_ids = set(patch_index)
    if supplemental_id in base_ids:
        base_ids.remove(supplemental_id)
    if len(base_ids) + 1 + len(static_sources) != expected_count:
        raise BuildError("profile corpus/translation count differs from table/HUD/static UI")
    if set(translation_index) != base_ids | {supplemental_id} | set(static_sources):
        raise BuildError("profile translation IDs differ from pinned corpus/map IDs")
    if static_sources:
        if not static_recipe or static_recipe.get("source_revision") != source_revision:
            raise BuildError("profile static recipe revision mismatch")
        fields = {}
        for obj in static_recipe["objects"]:
            for field in obj["fields"]:
                if field["type"] == "string":
                    identifier = f"cg:UnityUI:resources:{obj['path_id']}:m_Text"
                    if identifier in fields:
                        raise BuildError("duplicate static UI string target")
                    fields[identifier] = field
        if set(fields) != set(static_sources):
            raise BuildError("profile static recipe/source coverage mismatch")
        for identifier, source in static_sources.items():
            row = translation_index[identifier]
            if (source["source_revision"] != source_revision
                    or row.get("source_revision") != source_revision
                    or row.get("status") != "release_approved"
                    or row.get("source_sha256") != source["source"]["text_sha256"]
                    or fields[identifier]["before"] != source["source"]["text"]
                    or fields[identifier]["after"] != row.get("ko")):
                raise BuildError(f"profile static recipe differs from reviewed text: {identifier}")
    for identifier in base_ids:
        source = segment_index.get(identifier)
        patch = patch_index[identifier]
        if source is None:
            raise BuildError(f"profile source segment missing: {identifier}")
        source_info = source.get("source") or {}
        row = translation_index[identifier]
        if row.get("status") != "release_approved" or row.get("source_revision") != source_revision or source.get("source_revision") != source_revision:
            raise BuildError(f"profile translation/source approval mismatch: {identifier}")
        source_text = source_info.get("text")
        source_hash = source_info.get("text_sha256")
        if not isinstance(source_text, str) or not isinstance(source_hash, str) or sha256_bytes(source_text.encode()) != source_hash or row.get("source_sha256") != source_hash:
            raise BuildError(f"profile source hash mismatch: {identifier}")
        target_text = patch.get("target_text")
        if not isinstance(target_text, str) or patch.get("target_text_sha256") != sha256_bytes(target_text.encode()):
            raise BuildError(f"profile patch target hash mismatch: {identifier}")
    supplemental = translation_index[supplemental_id]
    if supplemental.get("status") != "release_approved" or supplemental.get("source_revision") != source_revision or not isinstance(supplemental.get("ko"), str):
        raise BuildError("profile supplemental translation is not currently release approved")
    if supplemental_source_path and supplemental_patch_path:
        ui_source = unique_index(read_rows(supplemental_source_path), "profile supplemental source")[supplemental_id]
        ui_patch = unique_index(read_rows(supplemental_patch_path), "profile supplemental patch-map")[supplemental_id]
        source_info = ui_source.get("source") or {}
        if ui_source.get("source_revision") != source_revision or supplemental.get("source_sha256") != source_info.get("text_sha256") or ui_patch.get("source_sha256") != supplemental.get("source_sha256"):
            raise BuildError("profile supplemental source/patch guard mismatch")
    base, ui = {key: translation_index[key] for key in base_ids}, supplemental
    base_hash, ui_hash = snapshot_part_hashes(translations_path, base_ids, supplemental_id)
    return base, ui, {
        "record_count": expected_count,
        "base_record_count": len(base_ids),
        "supplemental_record_count": 1,
        "static_ui_record_count": len(static_sources),
        "base_records_sha256": base_hash,
        "supplemental_record_sha256": ui_hash,
        "source_revision": source_revision,
    }


def make_edits(
    patches: list[dict[str, Any]],
    translations: dict[str, dict[str, Any]],
    *,
    target_text_path_ids: set[int] = set(TARGET_TEXT_PATH_IDS),
) -> dict[int, list[dict[str, Any]]]:
    grouped: dict[int, list[dict[str, Any]]] = {}
    for patch in patches:
        identifier = patch["id"]
        locator = patch["target_locator"]
        path_id = locator["asset_path_id"]
        edit = {
            "asset_path_id": path_id,
            "row_index": patch["target_row_index"],
            "column": locator["column"],
            "key": locator["key"],
            "occurrence": locator["occurrence"],
            "expected_sha256": patch["target_text_sha256"],
            "translation": translations[identifier]["ko"],
        }
        if patch.get("target_cell_present") is False:
            edit["target_cell_present"] = False
            insertion = patch.get("target_insertion")
            if not isinstance(insertion, dict):
                raise BuildError(f"{identifier}: missing target insertion evidence")
            edit["target_insertion"] = insertion
            if patch.get("asset_sha256"):
                edit["asset_sha256"] = patch["asset_sha256"]
        chinese = (patch.get("references") or {}).get("Chinese")
        if path_id in MULTILINGUAL_PATH_IDS or patch.get("source_column") in {"Chinese", "Japanese"}:
            if patch.get("source_column") == "Chinese":
                if not isinstance(chinese, str):
                    raise BuildError(f"{identifier}: invalid Chinese control source")
                if chinese:
                    edit["control_source_column"] = "Chinese"
                    edit["control_source_sha256"] = sha256_bytes(chinese.encode())
            elif patch.get("source_column") == "Japanese":
                if chinese not in {None, ""}:
                    raise BuildError(f"{identifier}: conflicting Japanese control source")
            else:
                raise BuildError(f"{identifier}: invalid multilingual control source")
        grouped.setdefault(path_id, []).append(edit)
    if set(grouped) != set(target_text_path_ids):
        raise BuildError("patch-map target assets differ from the pinned TextAssets")
    return grouped


def load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise BuildError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def verify_dependencies() -> None:
    for path, expected in DEPENDENCIES.items():
        if not path.is_file() or sha256_file(path) != expected:
            raise BuildError(f"fixed dependency hash mismatch: {path.relative_to(ROOT)}")


def validate_sources(source_game: Path) -> dict[str, str]:
    source_manifest = json.loads(SOURCE_MANIFEST.read_text(encoding="utf-8"))
    hash_manifest = json.loads(SOURCE_HASH_MANIFEST.read_text(encoding="utf-8"))
    if source_manifest.get("source_revision") != SOURCE_REVISION or hash_manifest.get("source_revision") != SOURCE_REVISION:
        raise BuildError("source revision mismatch")
    known = {item["path"]: item["copy_sha256"] for item in hash_manifest.get("files", [])}
    observed = {}
    for relative, expected in SOURCE_HASHES.items():
        path = source_game / relative
        if known.get(relative) != expected or not path.is_file() or sha256_file(path) != expected:
            raise BuildError(f"pristine source hash mismatch: {relative}")
        observed[relative] = expected
    return observed


def fresh_work_output(output: Path, work_root: Path = WORK_ROOT) -> tuple[Path, Path]:
    final = output.resolve()
    root = work_root.resolve()
    if final == root or not final.is_relative_to(root) or final.exists():
        raise BuildError("output must be a fresh child path under the project work directory")
    final.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{final.name}.", dir=final.parent))
    return final, stage


def _script_bytes(value: Any) -> bytes:
    return value if isinstance(value, bytes) else value.encode("utf-8", "surrogateescape")


def _outside_span_equal(source: Path, output: Path, offset: int, size: int) -> bool:
    with source.open("rb") as left, output.open("rb") as right:
        position = 0
        while True:
            a, b = left.read(8 << 20), right.read(8 << 20)
            if not a:
                return not b
            if len(a) != len(b):
                return False
            start, end = max(0, offset - position), min(len(a), offset + size - position)
            if start < end and a[:start] + a[end:] != b[:start] + b[end:]:
                return False
            if start >= end and a != b:
                return False
            position += len(a)


def build(
    source_game: Path,
    translations_path: Path,
    output: Path,
    *,
    ui_fixes: bool = False,
    ui_revision: str = "r04",
    profile: Path | None = None,
) -> dict[str, Any]:
    final, stage = fresh_work_output(output)
    try:
        profile_spec = None
        profile_paths_map: dict[str, Path] = {}
        source_revision = SOURCE_REVISION
        target_text_path_ids = set(TARGET_TEXT_PATH_IDS)
        resource_rel, ress_rel, font_rel = RESOURCE_REL, RESS_REL, FONT_REL
        profile_title = None
        profile_total_count = 8774
        if profile is not None:
            profile_module = _profile_module()
            try:
                profile_spec = profile_module.load_profile(profile, root=ROOT)
                profile_paths_map = profile_module.profile_paths(profile_spec)
            except Exception as exc:
                raise BuildError(f"profile validation failed: {exc}") from exc
            source_revision = profile_spec.source_revision
            target_text_path_ids = set(profile_spec.text_target_ids)
            profile_total_count = profile_spec.record_count
            profile_title = profile_spec.title
            resource_rel, ress_rel, font_rel = map(
                Path, profile_module.REQUIRED_SOURCE_PATHS
            )
            if profile_paths_map.get("translations"):
                translations_path = profile_paths_map["translations"]
            if not profile_paths_map.get("segments") or not profile_paths_map.get("patch_map"):
                raise BuildError("profile must bind segments and patch-map paths")
            ui_fixes = bool(profile_paths_map.get("ui_recipe"))
            if ui_fixes and not profile_paths_map.get("ui_source"):
                raise BuildError("profile UI recipe requires a bound supplemental source")
        elif ui_revision not in {"r04", "r05"} or (not ui_fixes and ui_revision != "r04"):
            raise BuildError("unsupported UI revision or missing --ui-fixes")
        dependency_hashes: dict[str, str]
        if profile_spec is None:
            verify_dependencies()
            dependency_hashes = {str(path.relative_to(ROOT)): expected for path, expected in DEPENDENCIES.items()}
        else:
            try:
                dependency_hashes = profile_module.verify_profile_dependencies(profile_spec, root=ROOT)
                profile_module.verify_profile_approvals(profile_spec, root=ROOT)
            except Exception as exc:
                raise BuildError(f"profile dependency validation failed: {exc}") from exc
        if profile_spec is not None:
            source_hashes = profile_module.validate_source_game(profile_spec, source_game)
        else:
            source_hashes = None
        recipe_path = UI_REPAIR_RECIPE if ui_revision == "r04" else UI_R05_RECIPE
        repair_dependencies = UI_REPAIR_DEPENDENCIES if ui_revision == "r04" else UI_R05_DEPENDENCIES
        repair_recipe = None
        if ui_fixes and profile_spec is None:
            if set(repair_dependencies) != {recipe_path, ROOT / "adapters/ui_maintenance.py"}:
                raise BuildError("UI repair dependencies are not pinned")
            for path, expected in repair_dependencies.items():
                if not path.is_file() or sha256_file(path) != expected:
                    raise BuildError(f"UI repair dependency hash mismatch: {path}")
            repair_recipe = json.loads(recipe_path.read_text(encoding="utf-8"))
            if repair_recipe.get("patch_revision") != ui_revision:
                raise BuildError("UI repair recipe revision mismatch")
            if repair_recipe.get("source_revision") != SOURCE_REVISION or repair_recipe.get("localized_record_count") != 4:
                raise BuildError("UI repair recipe source/count mismatch")
            validation = repair_recipe["translation_validation"]
            l10n = load_module("cg_l10n_maintenance", ROOT / "tools/l10n.py")
            qa = l10n.qa(ROOT, validation["source"], validation["translations"], validation["reviews"], complete=True, require_stage="l2")
            if not qa["ok"]:
                raise BuildError(f"UI repair translation QA/review failed: {qa['errors']}")
            context_hash = sha256_bytes(validation["context_file_bytes"].encode("utf-8"))
            translations = {row["id"]: row for row in validation["translations"]}
            sources = {row["id"]: row for row in validation["source"]}
            strings = [(row, field) for row in repair_recipe["objects"] for field in row["fields"] if field["type"] == "string"]
            if len(translations) != 4 or len(strings) != 4:
                raise BuildError("UI repair translation/field coverage mismatch")
            for row, field in strings:
                identifier = f"cg:UnityUI:resources:{row['path_id']}:m_Text"
                translation = translations[identifier]
                if translation["context_pack_digest"] != context_hash or translation["ko"] != field["after"] or sources[identifier]["source"]["text"] != field["before"]:
                    raise BuildError(f"UI repair field differs from reviewed translation: {identifier}")
        elif ui_fixes and profile_spec is not None:
            recipe_path = profile_paths_map["ui_recipe"]
            if not recipe_path.is_file():
                raise BuildError("profile UI recipe is missing")
            recipe_binding = profile_spec.raw.get("ui_recipe")
            recipe_hash = None
            if isinstance(recipe_binding, dict):
                recipe_hash = recipe_binding.get("sha256")
            recipe_hash = recipe_hash or profile_spec.raw.get("ui_recipe_sha256")
            if recipe_hash and sha256_file(recipe_path) != recipe_hash:
                raise BuildError("profile UI recipe dependency hash mismatch")
            repair_recipe = json.loads(recipe_path.read_text(encoding="utf-8"))
        source_game = source_game.resolve()
        if not source_game.is_dir() or final.is_relative_to(source_game) or source_game.is_relative_to(final):
            raise BuildError("source-game must be a separate existing read-only input directory")
        if profile_spec is None:
            source_hashes = validate_sources(source_game)
            patches, segments = read_rows(PATCH_MAP), read_rows(SEGMENTS)
            base, ui, snapshot = validate_snapshot(
                read_rows(translations_path), segments, patches, read_rows(UI_SOURCE), read_rows(UI_PATCH_MAP)
            )
        else:
            segments_path = profile_paths_map["segments"]
            patches_path = profile_paths_map["patch_map"]
            ui_source_path = profile_paths_map.get("ui_source")
            ui_patch_path = profile_paths_map.get("ui_patch_map")
            if (ui_source_path is None) != (ui_patch_path is None):
                raise BuildError("profile must bind both supplemental source and patch-map")
            base, ui, snapshot = validate_profile_snapshot(
                profile_spec,
                translations_path,
                segments_path,
                patches_path,
                supplemental_source_path=ui_source_path,
                supplemental_patch_path=ui_patch_path,
                static_source_path=profile_paths_map.get("ui_static_source"),
                static_recipe=repair_recipe,
            )
            patches, segments = read_rows(patches_path), read_rows(segments_path)
        if profile_spec is None:
            base_hash, ui_hash = snapshot_part_hashes(translations_path, set(base), HUD_ID)
            snapshot["base_records_sha256"] = base_hash
            snapshot["supplemental_record_sha256"] = ui_hash
        grouped = make_edits(patches, base, target_text_path_ids=target_text_path_ids)
        table = load_module("cg_table_maintenance", ROOT / "adapters/cavalry_girls.py")
        font = load_module("cg_font_maintenance", ROOT / "adapters/font_adapter.py")
        title_sprite = load_module("cg_title_maintenance", ROOT / "adapters/title_sprite.py")
        hud = load_module("cg_hud_maintenance", ROOT / "adapters/hud_adapter.py")

        source_bytes = (source_game / resource_rel).read_bytes()
        environment = UnityPy.load(source_bytes)
        serialized = environment.file
        serialized.name = "resources.assets"
        original_payloads = {obj.path_id: sha256_bytes(obj.get_raw_data()) for obj in environment.objects}
        metadata = {
            "format_version": serialized.header.version,
            "unity_version": serialized.unity_version,
            "externals": [str(item) for item in serialized.externals],
            "object_count": len(original_payloads),
        }
        text_records, expected_tables = [], {}
        for path_id, edits in sorted(grouped.items()):
            obj = serialized.objects[path_id]
            if obj.type.name != "TextAsset":
                raise BuildError(f"PathID {path_id} is not TextAsset")
            data = obj.read()
            before = obj.get_raw_data()
            data.save()
            if obj.data != before:
                raise BuildError(f"TextAsset no-op serialization drift: {path_id}")
            patched, record = table.patch_cells(
                _script_bytes(data.m_Script),
                edits,
                profile=profile_spec.raw if profile_spec is not None else None,
            )
            data.m_Script = patched.decode("utf-8")
            data.save()
            expected_tables[path_id] = patched
            text_records.append({
                "path_id": path_id,
                "name": data.m_Name,
                "requested_cell_count": record["requested_edit_count"],
                "changed_cell_count": record["changed_cell_count"],
                "script_after_sha256": sha256_bytes(patched),
            })
        adapter_profile = profile_spec.raw if profile_spec is not None else None
        font_record = (
            font.patch_fonts(environment, source_game / font_rel, profile=adapter_profile)
            if adapter_profile is not None
            else font.patch_fonts(environment, source_game / font_rel)
        )
        title_record = (
            title_sprite.patch_title_sprite(environment, profile=adapter_profile)
            if adapter_profile is not None
            else title_sprite.patch_title_sprite(environment)
        )
        hud_record = (
            hud.patch_hud(environment, ui["ko"], profile=adapter_profile)
            if adapter_profile is not None
            else hud.patch_hud(environment, ui["ko"])
        )
        repair_record = None
        if repair_recipe is not None:
            repair = load_module("cg_ui_maintenance", ROOT / "adapters/ui_maintenance.py")
            repair_record = repair.patch_ui(environment, repair_recipe)
        expected_payloads = {
            obj.path_id: sha256_bytes(obj.data if obj.data is not None else obj.get_raw_data())
            for obj in environment.objects
        }
        expected_changed = {pid for pid, before in original_payloads.items() if expected_payloads[pid] != before}
        allowed = target_text_path_ids | set(font_record["changed_path_ids"]) | set(title_record["changed_path_ids"]) | set(hud_record["changed_path_ids"])
        if repair_record is not None:
            allowed |= set(repair_record["changed_path_ids"])
        if not expected_changed.issubset(allowed):
            raise BuildError(f"unexpected in-memory object changes: {sorted(expected_changed-allowed)}")
        resource_output = stage / "resources.assets"
        resource_output.write_bytes(serialized.save())
        del serialized, environment, source_bytes
        gc.collect()

        rebuilt = UnityPy.load(resource_output.read_bytes())
        rebuilt.file.name = "resources.assets"
        actual_payloads = {obj.path_id: sha256_bytes(obj.get_raw_data()) for obj in rebuilt.objects}
        if actual_payloads != expected_payloads:
            raise BuildError("reload object payloads differ")
        if rebuilt.file.header.version != metadata["format_version"] or rebuilt.file.unity_version != metadata["unity_version"] or [str(x) for x in rebuilt.file.externals] != metadata["externals"]:
            raise BuildError("serialized file metadata changed")
        for path_id, expected in expected_tables.items():
            if _script_bytes(rebuilt.file.objects[path_id].read().m_Script) != expected:
                raise BuildError(f"reloaded table mismatch: {path_id}")
        reloaded_font = (
            font.patch_fonts(rebuilt, source_game / font_rel, profile=adapter_profile)
            if adapter_profile is not None
            else font.patch_fonts(rebuilt, source_game / font_rel)
        )
        if reloaded_font["changed_path_ids"]:
            raise BuildError("font patch is not idempotent after reload")
        reloaded_title = (
            title_sprite.patch_title_sprite(rebuilt, profile=adapter_profile)
            if adapter_profile is not None
            else title_sprite.patch_title_sprite(rebuilt)
        )
        if reloaded_title["changed_path_ids"]:
            raise BuildError("title Sprite patch is not idempotent after reload")
        reloaded_hud = (
            hud.patch_hud(rebuilt, ui["ko"], profile=adapter_profile)
            if adapter_profile is not None
            else hud.patch_hud(rebuilt, ui["ko"])
        )
        if reloaded_hud["changed_path_ids"]:
            raise BuildError("HUD patch is not idempotent after reload")
        if repair_recipe is not None and repair.patch_ui(rebuilt, repair_recipe)["changed_path_ids"]:
            raise BuildError("UI repair is not idempotent after reload")
        del rebuilt
        gc.collect()

        if profile_spec is None:
            title_path, expected_title_size, expected_title_hash = TITLE, 230_400, DEPENDENCIES[TITLE]
        else:
            title_binding = profile_title.get("payload") if profile_title else None
            if title_binding is None:
                raise BuildError("profile title payload path is missing")
            title_path = title_binding["path"]
            expected_title_size = int(profile_title["size"])
            expected_title_hash = profile_title.get("payload_sha256") or (title_binding or {}).get("sha256")
            if not expected_title_hash:
                raise BuildError("profile title payload has no sha256 binding")
        title = title_path.read_bytes()
        if len(title) != expected_title_size or sha256_bytes(title) != expected_title_hash:
            raise BuildError("Korean title payload mismatch")
        ress_source, ress_output = source_game / ress_rel, stage / "resources.assets.resS"
        shutil.copy2(ress_source, ress_output)
        title_offset = TITLE_OFFSET if profile_spec is None else int(profile_title["offset"])
        original_title_hash = TITLE_ORIGINAL_SHA256 if profile_spec is None else profile_title.get("original_sha256")
        if not original_title_hash:
            raise BuildError("profile title original span hash is missing")
        with ress_output.open("r+b") as stream:
            stream.seek(title_offset)
            if sha256_bytes(stream.read(len(title))) != original_title_hash:
                raise BuildError("original title span mismatch")
            stream.seek(title_offset)
            stream.write(title)
        if not _outside_span_equal(ress_source, ress_output, title_offset, len(title)):
            raise BuildError("resources.assets.resS changed outside title span")

        outputs = {
            path.name: {"sha256": sha256_file(path), "size": path.stat().st_size}
            for path in (resource_output, ress_output)
        }
        manifest = {
            "schema_version": "1.0.0",
            "source_revision": source_revision,
            "builder": {"path": "tools/maintenance/build.py", "sha256": sha256_file(Path(__file__)), "unitypy": UnityPy.__version__},
            "translation_snapshot": {"path": str(translations_path.resolve()), "sha256": sha256_file(translations_path), **snapshot},
            "source_hashes": source_hashes,
            "source_protected_read_only": True,
            "original_serialized_metadata": metadata,
            "changed_path_ids": sorted(expected_changed),
            "unchanged_object_count": len(original_payloads) - len(expected_changed),
            "all_object_payloads_verified": True,
            "text_records": text_records,
            "fonts": font_record,
            "title_sprite": title_record,
            "supplemental_ui": {"id": profile_spec.raw.get("supplemental_id", HUD_ID) if profile_spec is not None else HUD_ID, "patch": hud_record},
            "patch_revision": "r06" if profile_spec is not None else (ui_revision if ui_fixes else "r03"),
            "total_localized_record_count": profile_total_count if profile_spec is not None else (8778 if ui_fixes else 8774),
            "external_title": {"offset": title_offset, "size": len(title), "payload_sha256": sha256_bytes(title), "outside_span_equal": True},
            "outputs": outputs,
            "runtime_verified": False,
            "release_scope": "stage8_local_candidate",
        }
        if profile_spec is not None:
            manifest["profile"] = {
                "path": str(profile_spec.path.relative_to(ROOT)),
                "sha256": sha256_file(profile_spec.path),
                "version": profile_spec.version,
                "source_revision": profile_spec.source_revision,
                "source_hash_manifest_sha256": profile_spec.source_manifest_sha256,
                "dependency_hashes": dependency_hashes,
                "coverage": profile_spec.coverage,
                "artifact_digests": {
                    key: value["sha256"] for key, value in profile_spec.artifacts.items()
                },
            }
        if repair_record is not None:
            manifest["maintenance_ui"] = {
                "recipe_path": recipe_path.relative_to(ROOT).as_posix(),
                "recipe_sha256": sha256_file(recipe_path),
                "adapter_sha256": sha256_file(ROOT / "adapters/ui_maintenance.py"),
                "localized_record_count": snapshot.get("static_ui_record_count", 4),
                "patch": repair_record,
            }
        (stage / "build-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        stage.rename(final)
        return {"output": str(final), "manifest": manifest}
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-game", type=Path, required=True)
    parser.add_argument("--translations", type=Path, default=None)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ui-fixes", action="store_true", help="apply the fixed r04 screenshot UI repairs")
    parser.add_argument("--ui-revision", choices=("r04", "r05"), default="r04")
    parser.add_argument("--profile", type=Path, default=None, help="validated version profile (requires explicit --source-game)")
    args = parser.parse_args()
    source_game_arg = any(arg == "--source-game" or arg.startswith("--source-game=") for arg in sys.argv[1:])
    if args.profile is not None and not source_game_arg:
        parser.error("--profile requires explicit --source-game")
    source_game = args.source_game
    translations = args.translations or DEFAULT_TRANSLATIONS
    try:
        result = build(source_game, translations, args.output, ui_fixes=args.ui_fixes, ui_revision=args.ui_revision, profile=args.profile)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, "output": result["output"], "outputs": result["manifest"]["outputs"], "runtime_verified": False}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
