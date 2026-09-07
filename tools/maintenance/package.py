#!/usr/bin/env python3
"""Create a complete guarded Windows xdelta package from a maintenance build."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
WORK_ROOT = ROOT / "work"
INSTALLER_ROOT = ROOT / "release/installer"
R04_RECIPE = ROOT / "release/reference/ui-r04.json"
R04_RECIPE_SHA256 = "e34c37a742aaace0db9ec1590cc42c04f38563efdf16162d8daaf2e8a295391f"
UI_RECIPES = {
    "r04": (R04_RECIPE, R04_RECIPE_SHA256),
    "r05": (ROOT / "release/reference/ui-r05.json", "4b62db744834b7942bc399d46116e3bef3283096688e26e1dd40d5a46a0fb779"),
}

SOURCE_REVISION = "f1c8fc8f5e5f7470da9ca0ad5a90bca21ab4d75c71e65509b2ffaf75b579eb6a"
TARGETS = (
    {"name": "resources.assets", "game_path": "CavalryGirls_Data/resources.assets", "source_sha256": "d275481b891eef752e5d4279c587b564db4c5ef5e0291cc80af82a089d793838"},
    {"name": "resources.assets.resS", "game_path": "CavalryGirls_Data/resources.assets.resS", "source_sha256": "8d60f1b9828c759f6483fa1998def795c9ed84dd0cd653950e2ee12b2acc6026"},
)
INSTALLER_FILES = {
    "Install-KoreanPatch.ps1": "d838fab3553d6a3ea4840ae93bbc86c12851ac0c90f2a3d59d19bebe1369a7a0",
    "install.cmd": "f5ff7eb2337ee424c01a2315f8d1125ca13000bdb7d3340cc437c2db32db2c92",
    "restore.cmd": "8e58a4991dba06ccea6d1f6a0093b456be4d28afa3adb3cf32d6e2881fbcb402",
    "Test-Installer.ps1": "a921da8495e483721a581d3f93fb910f113fdffb9593bd4d8bd8aa13bfa626d4",
    "README.ko.md": "9f9a55586da50b565288dfb5afcc41dfd669298aad83878f860327bdfa30852b",
    "MANUAL-TEST.ko.md": "d7cec3cad4884d53bda4bef206a94c7a7dde39f57f245f95293e7262fbb2c180",
    "bin/xdelta3.exe": "53d90226615f217d3380c39892833311b4e24acd863e1ca01f14b5e772e2e6d0",
    "licenses/xdelta-LICENSE.txt": "6cf63d87586c7c25c3ab8e62eef5c75bbaa982b0a6f9c00c59e2720255aac9ec",
    "licenses/THIRD-PARTY.md": "80d96e3352dc7516b97c4de78f5dd3df31b095b0e939015a2246fe3be899d00a",
}


class PackageError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_installer_tree(root: Path, expected: dict[str, str] | None = INSTALLER_FILES) -> list[str]:
    missing = [relative for relative in INSTALLER_FILES if not (root / relative).is_file()]
    if missing:
        raise PackageError(f"installer files missing: {missing}")
    if expected is not None:
        for relative, digest in expected.items():
            if sha256_file(root / relative) != digest:
                raise PackageError(f"installer dependency hash mismatch: {relative}")
    return sorted(INSTALLER_FILES)


def resolve_fresh_work_output(output: Path, work_root: Path = WORK_ROOT) -> Path:
    final, root = output.resolve(), work_root.resolve()
    if final == root or not final.is_relative_to(root) or final.exists():
        raise PackageError("output must be a fresh child path under the project work directory")
    return final


def validate_build(build_dir: Path, source_game: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    build_dir = build_dir.resolve()
    if not build_dir.is_relative_to(WORK_ROOT.resolve()):
        raise PackageError("build must be under the project work directory")
    manifest_path = build_dir / "build-manifest.json"
    if not manifest_path.is_file():
        raise PackageError("build-manifest.json is missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    snapshot = manifest.get("translation_snapshot") or {}
    revision = manifest.get("patch_revision")
    total = 8778 if revision in UI_RECIPES else 8774
    if (
        manifest.get("source_revision") != SOURCE_REVISION
        or manifest.get("runtime_verified") is not False
        or manifest.get("release_scope") != "stage8_local_candidate"
        or manifest.get("source_protected_read_only") is not True
        or manifest.get("all_object_payloads_verified") is not True
        or revision not in {"r03", *UI_RECIPES}
        or manifest.get("total_localized_record_count") != total
        or snapshot.get("record_count") != 8774
        or snapshot.get("base_record_count") != 8773
        or snapshot.get("supplemental_record_count") != 1
    ):
        raise PackageError("build manifest release/snapshot contract mismatch")
    if revision in UI_RECIPES:
        recipe_path, recipe_hash = UI_RECIPES[revision]
        repair = manifest.get("maintenance_ui") or {}
        if not recipe_path.is_file() or sha256_file(recipe_path) != recipe_hash:
            raise PackageError("r04 recipe dependency hash mismatch")
        recipe = json.loads(recipe_path.read_text(encoding="utf-8"))
        expected_ids = sorted(row["path_id"] for row in recipe["objects"])
        patch = repair.get("patch") or {}
        if repair.get("recipe_sha256") != recipe_hash or repair.get("localized_record_count") != 4 or patch.get("target_path_ids") != expected_ids or patch.get("runtime_verified") is not False:
            raise PackageError("r04 UI repair evidence mismatch")
    text_records = manifest.get("text_records")
    if not isinstance(text_records, list) or {row.get("path_id") for row in text_records} != {6326, 6427, 6402, 6329, 6351} or sum(row.get("requested_cell_count", 0) for row in text_records) != 8773:
        raise PackageError("build text record coverage mismatch")
    if not ((manifest.get("fonts") or {}).get("validation") or {}).get("local_pptr_resolved"):
        raise PackageError("font validation missing")
    if not (manifest.get("external_title") or {}).get("outside_span_equal"):
        raise PackageError("title payload validation missing")
    if (manifest.get("supplemental_ui") or {}).get("id") != "cg:UnityUI:resources:324693:m_Text":
        raise PackageError("supplemental HUD validation missing")
    outputs = manifest.get("outputs")
    if not isinstance(outputs, dict) or set(outputs) != {row["name"] for row in TARGETS}:
        raise PackageError("build outputs mismatch")
    verified = []
    for target in TARGETS:
        source, built = source_game / target["game_path"], build_dir / target["name"]
        record = outputs[target["name"]]
        if not source.is_file() or sha256_file(source) != target["source_sha256"]:
            raise PackageError(f"pristine source mismatch: {target['game_path']}")
        if not built.is_file() or built.stat().st_size != record.get("size") or sha256_file(built) != record.get("sha256"):
            raise PackageError(f"built output mismatch: {target['name']}")
        if record["sha256"] == target["source_sha256"]:
            raise PackageError(f"built output is unchanged: {target['name']}")
        verified.append({**target, "source": source, "built": built, "source_size": source.stat().st_size, "built_size": built.stat().st_size, "built_sha256": record["sha256"]})
    return manifest, verified


def _run(command: list[str]) -> None:
    completed = subprocess.run(command, capture_output=True, text=True)
    if completed.returncode:
        raise PackageError(f"xdelta3 failed ({completed.returncode}): {(completed.stderr or completed.stdout).strip()}")


def package(build_dir: Path, source_game: Path, output: Path) -> dict[str, Any]:
    final = resolve_fresh_work_output(output)
    source_game = source_game.resolve()
    if not source_game.is_dir() or final.is_relative_to(source_game) or source_game.is_relative_to(final):
        raise PackageError("source-game must be a separate existing read-only input directory")
    validate_installer_tree(INSTALLER_ROOT)
    manifest, targets = validate_build(build_dir, source_game)
    executable = shutil.which("xdelta3")
    if executable is None:
        raise PackageError("xdelta3 is required on PATH for packaging")
    final.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{final.name}.", dir=final.parent))
    try:
        packaged_targets = []
        with tempfile.TemporaryDirectory(prefix="decode-check-", dir=stage) as temp_name:
            scratch = Path(temp_name)
            for target in targets:
                delta = stage / (target["name"] + ".xdelta3")
                decoded = scratch / target["name"]
                _run([executable, "-f", "-e", "-s", str(target["source"]), str(target["built"]), str(delta)])
                _run([executable, "-f", "-d", "-s", str(target["source"]), str(delta), str(decoded)])
                if decoded.stat().st_size != target["built_size"] or sha256_file(decoded) != target["built_sha256"]:
                    raise PackageError(f"delta decode mismatch: {target['name']}")
                packaged_targets.append({
                    "game_path": target["game_path"], "source_sha256": target["source_sha256"], "source_size": target["source_size"],
                    "built_sha256": target["built_sha256"], "built_size": target["built_size"], "delta": delta.name,
                    "delta_sha256": sha256_file(delta), "delta_size": delta.stat().st_size,
                    "decode_sha256": target["built_sha256"], "decode_size": target["built_size"],
                })
        for relative in validate_installer_tree(INSTALLER_ROOT):
            destination = stage / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(INSTALLER_ROOT / relative, destination)
        package_manifest = {
            "schema_version": "1.0.0", "source_revision": SOURCE_REVISION,
            "build_manifest_sha256": sha256_file(build_dir / "build-manifest.json"),
            "build_translation_snapshot_sha256": manifest["translation_snapshot"]["sha256"],
            "targets": packaged_targets,
            "installer": {"path": "Install-KoreanPatch.ps1", "sha256": sha256_file(stage / "Install-KoreanPatch.ps1")},
            "installer_test": {"path": "Test-Installer.ps1", "sha256": sha256_file(stage / "Test-Installer.ps1")},
            "instructions": {"path": "README.ko.md", "sha256": sha256_file(stage / "README.ko.md")},
            "manual_test": {"path": "MANUAL-TEST.ko.md", "sha256": sha256_file(stage / "MANUAL-TEST.ko.md")},
            "decoder": {"path": "bin/xdelta3.exe", "sha256": sha256_file(stage / "bin/xdelta3.exe"), "version": "3.2.0", "license": "Apache-2.0"},
            "entrypoints": {name: sha256_file(stage / name) for name in ("install.cmd", "restore.cmd")},
            "xdelta3": {"executable_required": False, "encoder_path": executable, "decode_verified": True, "bundled": True},
            "backup_directory": ".cavalry-girls-ko-backup", "runtime_verified": False,
            "release_scope": "stage8_local_candidate", "patch_revision": manifest["patch_revision"], "total_localized_record_count": manifest["total_localized_record_count"],
            "base_translation_snapshot_sha256": manifest["translation_snapshot"]["base_records_sha256"],
            "supplemental_ui_snapshot_sha256": manifest["translation_snapshot"]["supplemental_record_sha256"],
            "game_version": "2.6.2859", "steam_build_id": "24639430", "app_id": "2055050", "runtime_verifier": "human",
            "known_debt": ["Final full-build gameplay/display/save reload requires human validation.", "Opaque Enc/ArtBook and non-title image text remain outside scope."],
        }
        if manifest["patch_revision"] in UI_RECIPES:
            package_manifest["maintenance_ui"] = manifest["maintenance_ui"]
            package_manifest["known_debt"].append("r04: verify long weapon tooltip height and upgrade/reforge/duplicate labels in initial and populated states.")
        manifest_path = stage / "package-manifest.json"
        manifest_path.write_text(json.dumps(package_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        checksums = []
        for path in sorted((item for item in stage.rglob("*") if item.is_file()), key=lambda item: item.relative_to(stage).as_posix()):
            checksums.append(f"{sha256_file(path)}  {path.relative_to(stage).as_posix()}")
        (stage / "SHA256SUMS.txt").write_text("\n".join(checksums) + "\n", encoding="ascii")
        stage.rename(final)
        return {"output": str(final), "manifest": package_manifest}
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--build", type=Path, required=True)
    parser.add_argument("--source-game", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = package(args.build, args.source_game, args.output)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, "output": result["output"], "targets": 2, "runtime_verified": False}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
