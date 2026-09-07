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
    ROOT / "adapters/cavalry_girls.py": "1dc773e0ea8a6dba80cf7e05f71bc0466b1a20af0e95332e239755938f78d4c4",
    ROOT / "adapters/font_adapter.py": "a2f9b728352ff14c6e3d3fbf2256e33fa4472e62465af57098b5c9746fbcb03e",
    ROOT / "adapters/title_sprite.py": "b46a493a073c398ca7bf4220e4b21d3403a6124837ad60229eb79585ffacdb5d",
    ROOT / "adapters/hud_adapter.py": "a7c27fa4b466a4537e7ccb3844971eb508f0eb503ccc80194e28aac25f82a580",
    ROOT / "release/assets/title-ko.dxt5": "646b631b7bc1b4399f52fc673ee2f9c2f26c8d0600a47c8a8e096107ba303daf",
    ROOT / "release/reference/source-hashes.json": "f7cfcf1991c2a5345a6d623f7bdcbf46760fb3079759420b017584b992af4591",
    ROOT / "localization/corpus/source-manifest.json": "243a8394f0250f52fa2d98bcf1b9a010d5451af10f73cf43c71b6867290cd2e7",
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
    ROOT / "adapters/ui_maintenance.py": "87f6c6499ea3d4628474d8068b8c9320385d717a7273843506da7869c1cf1f99",
}

UI_R05_RECIPE = ROOT / "release/reference/ui-r05.json"
UI_R05_DEPENDENCIES = {
    UI_R05_RECIPE: "4b62db744834b7942bc399d46116e3bef3283096688e26e1dd40d5a46a0fb779",
    ROOT / "adapters/ui_maintenance.py": UI_REPAIR_DEPENDENCIES[ROOT / "adapters/ui_maintenance.py"],
}


class BuildError(RuntimeError):
    pass


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
) -> tuple[dict[str, dict[str, Any]], dict[str, Any], dict[str, Any]]:
    segment_index = unique_index(segments, "segment")
    patch_index = unique_index(patches, "patch-map")
    ui_source_index = unique_index(ui_sources, "supplemental source")
    ui_patch_index = unique_index(ui_patches, "supplemental patch-map")
    if len(patch_index) != 8773 or set(segment_index) != set(patch_index):
        raise BuildError("base corpus/patch-map must contain the same 8773 ids")
    if set(ui_source_index) != {HUD_ID} or set(ui_patch_index) != {HUD_ID}:
        raise BuildError("supplemental corpus/patch-map must contain only the HUD id")
    base, supplemental = split_snapshot(translations, set(patch_index), {HUD_ID})
    context_pairs: set[tuple[str, str]] = set()
    revisions: set[str] = set()
    for identifier, row in base.items():
        source = segment_index[identifier]
        source_info = source.get("source") or {}
        patch = patch_index[identifier]
        if (
            row.get("source_revision") != SOURCE_REVISION
            or source.get("source_revision") != SOURCE_REVISION
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
    ui = supplemental[HUD_ID]
    ui_source = ui_source_index[HUD_ID]
    ui_info = ui_source.get("source") or {}
    ui_patch = ui_patch_index[HUD_ID]
    if (
        ui.get("source_revision") != SOURCE_REVISION
        or ui_source.get("source_revision") != SOURCE_REVISION
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
        "record_count": 8774,
        "base_record_count": 8773,
        "supplemental_record_count": 1,
        "context_packs": [
            {"context_pack_id": left, "context_pack_digest": right}
            for left, right in sorted(context_pairs)
        ],
        "translation_revisions": sorted(revisions),
    }


def make_edits(patches: list[dict[str, Any]], translations: dict[str, dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
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
        chinese = (patch.get("references") or {}).get("Chinese")
        if path_id in MULTILINGUAL_PATH_IDS:
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
    if set(grouped) != TARGET_TEXT_PATH_IDS:
        raise BuildError("patch-map target assets differ from the fixed five TextAssets")
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


def build(source_game: Path, translations_path: Path, output: Path, *, ui_fixes: bool = False, ui_revision: str = "r04") -> dict[str, Any]:
    final, stage = fresh_work_output(output)
    try:
        verify_dependencies()
        if ui_revision not in {"r04", "r05"} or (not ui_fixes and ui_revision != "r04"):
            raise BuildError("unsupported UI revision or missing --ui-fixes")
        recipe_path = UI_REPAIR_RECIPE if ui_revision == "r04" else UI_R05_RECIPE
        repair_dependencies = UI_REPAIR_DEPENDENCIES if ui_revision == "r04" else UI_R05_DEPENDENCIES
        repair_recipe = None
        if ui_fixes:
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
        source_game = source_game.resolve()
        if not source_game.is_dir() or final.is_relative_to(source_game) or source_game.is_relative_to(final):
            raise BuildError("source-game must be a separate existing read-only input directory")
        source_hashes = validate_sources(source_game)
        patches, segments = read_rows(PATCH_MAP), read_rows(SEGMENTS)
        base, ui, snapshot = validate_snapshot(
            read_rows(translations_path), segments, patches, read_rows(UI_SOURCE), read_rows(UI_PATCH_MAP)
        )
        base_hash, ui_hash = snapshot_part_hashes(translations_path, set(base), HUD_ID)
        snapshot["base_records_sha256"] = base_hash
        snapshot["supplemental_record_sha256"] = ui_hash
        grouped = make_edits(patches, base)
        table = load_module("cg_table_maintenance", ROOT / "adapters/cavalry_girls.py")
        font = load_module("cg_font_maintenance", ROOT / "adapters/font_adapter.py")
        title_sprite = load_module("cg_title_maintenance", ROOT / "adapters/title_sprite.py")
        hud = load_module("cg_hud_maintenance", ROOT / "adapters/hud_adapter.py")

        source_bytes = (source_game / RESOURCE_REL).read_bytes()
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
            patched, record = table.patch_cells(_script_bytes(data.m_Script), edits)
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
        font_record = font.patch_fonts(environment, source_game / FONT_REL)
        title_record = title_sprite.patch_title_sprite(environment)
        hud_record = hud.patch_hud(environment, ui["ko"])
        repair_record = None
        if repair_recipe is not None:
            repair = load_module("cg_ui_maintenance", ROOT / "adapters/ui_maintenance.py")
            repair_record = repair.patch_ui(environment, repair_recipe)
        expected_payloads = {
            obj.path_id: sha256_bytes(obj.data if obj.data is not None else obj.get_raw_data())
            for obj in environment.objects
        }
        expected_changed = {pid for pid, before in original_payloads.items() if expected_payloads[pid] != before}
        allowed = TARGET_TEXT_PATH_IDS | set(font_record["changed_path_ids"]) | set(title_record["changed_path_ids"]) | set(hud_record["changed_path_ids"])
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
        if font.patch_fonts(rebuilt, source_game / FONT_REL)["changed_path_ids"]:
            raise BuildError("font patch is not idempotent after reload")
        if title_sprite.patch_title_sprite(rebuilt)["changed_path_ids"]:
            raise BuildError("title Sprite patch is not idempotent after reload")
        if hud.patch_hud(rebuilt, ui["ko"])["changed_path_ids"]:
            raise BuildError("HUD patch is not idempotent after reload")
        if repair_recipe is not None and repair.patch_ui(rebuilt, repair_recipe)["changed_path_ids"]:
            raise BuildError("UI repair is not idempotent after reload")
        del rebuilt
        gc.collect()

        title = TITLE.read_bytes()
        if len(title) != 230_400 or sha256_bytes(title) != DEPENDENCIES[TITLE]:
            raise BuildError("Korean title payload mismatch")
        ress_source, ress_output = source_game / RESS_REL, stage / "resources.assets.resS"
        shutil.copy2(ress_source, ress_output)
        with ress_output.open("r+b") as stream:
            stream.seek(TITLE_OFFSET)
            if sha256_bytes(stream.read(len(title))) != TITLE_ORIGINAL_SHA256:
                raise BuildError("original title span mismatch")
            stream.seek(TITLE_OFFSET)
            stream.write(title)
        if not _outside_span_equal(ress_source, ress_output, TITLE_OFFSET, len(title)):
            raise BuildError("resources.assets.resS changed outside title span")

        outputs = {
            path.name: {"sha256": sha256_file(path), "size": path.stat().st_size}
            for path in (resource_output, ress_output)
        }
        manifest = {
            "schema_version": "1.0.0",
            "source_revision": SOURCE_REVISION,
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
            "supplemental_ui": {"id": HUD_ID, "patch": hud_record},
            "patch_revision": ui_revision if ui_fixes else "r03",
            "total_localized_record_count": 8778 if ui_fixes else 8774,
            "external_title": {"offset": TITLE_OFFSET, "size": len(title), "payload_sha256": sha256_bytes(title), "outside_span_equal": True},
            "outputs": outputs,
            "runtime_verified": False,
            "release_scope": "stage8_local_candidate",
        }
        if repair_record is not None:
            manifest["maintenance_ui"] = {
                "recipe_path": recipe_path.relative_to(ROOT).as_posix(),
                "recipe_sha256": sha256_file(recipe_path),
                "adapter_sha256": sha256_file(ROOT / "adapters/ui_maintenance.py"),
                "localized_record_count": 4,
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
    parser.add_argument("--translations", type=Path, default=DEFAULT_TRANSLATIONS)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ui-fixes", action="store_true", help="apply the fixed r04 screenshot UI repairs")
    parser.add_argument("--ui-revision", choices=("r04", "r05"), default="r04")
    args = parser.parse_args()
    try:
        result = build(args.source_game, args.translations, args.output, ui_fixes=args.ui_fixes, ui_revision=args.ui_revision)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, "output": result["output"], "outputs": result["manifest"]["outputs"], "runtime_verified": False}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
