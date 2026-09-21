#!/usr/bin/env python3
"""Create a complete guarded Windows xdelta package from a maintenance build."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
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
    "README.ko.md": "7e729e1d43151d21d9d21814a1c619d69a00d2d53e7edbbb904e271a01968697",
    "MANUAL-TEST.ko.md": "8b5bc95278efca41d07a4503f8677a4ffe4d2134db554e930668ca474018cfe9",
    "bin/xdelta3.exe": "53d90226615f217d3380c39892833311b4e24acd863e1ca01f14b5e772e2e6d0",
    "licenses/xdelta-LICENSE.txt": "6cf63d87586c7c25c3ab8e62eef5c75bbaa982b0a6f9c00c59e2720255aac9ec",
    "licenses/THIRD-PARTY.md": "80d96e3352dc7516b97c4de78f5dd3df31b095b0e939015a2246fe3be899d00a",
}


class PackageError(RuntimeError):
    pass


def _profile_module() -> Any:
    spec = importlib.util.spec_from_file_location("cg_package_profile", ROOT / "tools/maintenance/profile.py")
    if spec is None or spec.loader is None:
        raise PackageError("cannot load profile validator")
    module = importlib.util.module_from_spec(spec)
    sys.modules["cg_package_profile"] = module
    spec.loader.exec_module(module)
    return module


def render_profile_installer(template: str, profile: Any) -> str:
    """Bind the staged installer to the same pristine inputs as this package."""
    replacements = {SOURCE_REVISION: profile.source_revision}
    for target in TARGETS:
        relative = target["game_path"]
        replacements[target["source_sha256"]] = profile.source_hashes[relative]
    for before, after in replacements.items():
        if template.count(before) != 1:
            raise PackageError("installer version/hash anchor is missing or ambiguous")
        template = template.replace(before, after, 1)
    return template


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


def validate_build(
    build_dir: Path,
    source_game: Path,
    *,
    profile: Any = None,
    profile_dependency_hashes: dict[str, str] | None = None,
    profile_paths_map: dict[str, Path] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    build_dir = build_dir.resolve()
    if not build_dir.is_relative_to(WORK_ROOT.resolve()):
        raise PackageError("build must be under the project work directory")
    manifest_path = build_dir / "build-manifest.json"
    if not manifest_path.is_file():
        raise PackageError("build-manifest.json is missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    snapshot = manifest.get("translation_snapshot") or {}
    revision = manifest.get("patch_revision")
    profile_mode = profile is not None
    profile_version = getattr(profile, "version", None)
    profile_revision = getattr(profile, "source_revision", None)
    if profile_mode:
        expected_source_revision = profile_revision
        expected_total = profile.record_count
        expected_targets = {
            "resources.assets": profile.source_hashes["CavalryGirls_Data/resources.assets"],
            "resources.assets.resS": profile.source_hashes["CavalryGirls_Data/resources.assets.resS"],
        }
    else:
        expected_source_revision = SOURCE_REVISION
        expected_total = 8778 if revision in UI_RECIPES else 8774
        expected_targets = {row["name"]: row["source_sha256"] for row in TARGETS}
    if (
        manifest.get("source_revision") != expected_source_revision
        or manifest.get("runtime_verified") is not False
        or manifest.get("release_scope") != "stage8_local_candidate"
        or manifest.get("source_protected_read_only") is not True
        or manifest.get("all_object_payloads_verified") is not True
        or (revision not in {"r03", *UI_RECIPES} if not profile_mode else revision != "r06")
        or manifest.get("total_localized_record_count") != expected_total
        or (snapshot.get("record_count") != 8774 if not profile_mode else snapshot.get("record_count") != expected_total)
        or (snapshot.get("base_record_count") != 8773 if not profile_mode else snapshot.get("base_record_count") != expected_total - 1 - snapshot.get("static_ui_record_count", 0))
        or snapshot.get("supplemental_record_count") != 1
    ):
        raise PackageError("build manifest release/snapshot contract mismatch")
    if profile_mode:
        if profile_dependency_hashes is None or profile_paths_map is None:
            raise PackageError("profile dependency/path bindings are required")
        bound = manifest.get("profile") or {}
        if bound.get("version") != profile_version or bound.get("source_revision") != profile_revision:
            raise PackageError("build profile identity mismatch")
        if bound.get("sha256") != sha256_file(profile.path):
            raise PackageError("build profile digest mismatch")
        if bound.get("source_hash_manifest_sha256") != profile.source_manifest_sha256:
            raise PackageError("build source hash manifest digest mismatch")
        expected_artifacts = {
            key: value["sha256"] for key, value in profile.artifacts.items()
        }
        if (
            bound.get("coverage") != profile.coverage
            or bound.get("artifact_digests") != expected_artifacts
            or bound.get("dependency_hashes") != profile_dependency_hashes
        ):
            raise PackageError("build profile coverage/artifact/dependency binding mismatch")
        recipe_path = profile_paths_map.get("ui_recipe")
        if recipe_path is not None:
            recipe = json.loads(recipe_path.read_text(encoding="utf-8"))
            expected_ids = sorted(row["path_id"] for row in recipe["objects"])
            repair = manifest.get("maintenance_ui") or {}
            patch = repair.get("patch") or {}
            expected_recipe_count = recipe.get("localized_record_count")
            if (
                repair.get("recipe_sha256") != sha256_file(recipe_path)
                or repair.get("adapter_sha256") != profile_dependency_hashes.get("adapters/ui_maintenance.py")
                or repair.get("localized_record_count") != expected_recipe_count
                or snapshot.get("static_ui_record_count") != expected_recipe_count
                or patch.get("target_path_ids") != expected_ids
                or patch.get("runtime_verified") is not False
            ):
                raise PackageError("profile maintenance UI evidence mismatch")
        elif manifest.get("maintenance_ui") is not None or snapshot.get("static_ui_record_count", 0):
            raise PackageError("unexpected profile maintenance UI evidence")
    if revision in UI_RECIPES and not profile_mode:
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
    if not isinstance(text_records, list):
        raise PackageError("build text record coverage mismatch")
    if profile_mode:
        if {row.get("path_id") for row in text_records} != set(profile.text_target_ids):
            raise PackageError("profile TextAsset target coverage mismatch")
        requested = sum(row.get("requested_cell_count", 0) for row in text_records)
        if requested != snapshot.get("base_record_count"):
            raise PackageError("profile text record count mismatch")
    elif {row.get("path_id") for row in text_records} != {6326, 6427, 6402, 6329, 6351} or sum(row.get("requested_cell_count", 0) for row in text_records) != 8773:
        raise PackageError("build text record coverage mismatch")
    if not ((manifest.get("fonts") or {}).get("validation") or {}).get("local_pptr_resolved"):
        raise PackageError("font validation missing")
    if not (manifest.get("external_title") or {}).get("outside_span_equal"):
        raise PackageError("title payload validation missing")
    expected_supplemental = (
        (profile.raw.get("supplemental_id") if profile_mode else None)
        or "cg:UnityUI:resources:324693:m_Text"
    )
    if (manifest.get("supplemental_ui") or {}).get("id") != expected_supplemental:
        raise PackageError("supplemental HUD validation missing")
    outputs = manifest.get("outputs")
    if not isinstance(outputs, dict) or set(outputs) != set(expected_targets):
        raise PackageError("build outputs mismatch")
    verified = []
    targets_to_check = (
        [{"name": name, "game_path": "CavalryGirls_Data/" + name, "source_sha256": digest} for name, digest in expected_targets.items()]
        if profile_mode else list(TARGETS)
    )
    for target in targets_to_check:
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


def package(
    build_dir: Path,
    source_game: Path,
    output: Path,
    *,
    profile: Path | None = None,
) -> dict[str, Any]:
    final = resolve_fresh_work_output(output)
    source_game = source_game.resolve()
    if not source_game.is_dir() or final.is_relative_to(source_game) or source_game.is_relative_to(final):
        raise PackageError("source-game must be a separate existing read-only input directory")
    profile_spec = None
    profile_dependency_hashes = None
    profile_paths_map = None
    if profile is not None:
        profile_module = _profile_module()
        try:
            profile_spec = profile_module.load_profile(profile, root=ROOT)
            profile_dependency_hashes = profile_module.verify_profile_dependencies(
                profile_spec, root=ROOT
            )
            profile_paths_map = profile_module.profile_paths(profile_spec, root=ROOT)
            profile_module.verify_profile_approvals(profile_spec, root=ROOT)
            profile_module.validate_source_game(profile_spec, source_game)
        except Exception as exc:
            raise PackageError(f"profile validation failed: {exc}") from exc
    validate_installer_tree(INSTALLER_ROOT)
    manifest, targets = validate_build(
        build_dir,
        source_game,
        profile=profile_spec,
        profile_dependency_hashes=profile_dependency_hashes,
        profile_paths_map=profile_paths_map,
    )
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
        if profile_spec is not None:
            installer = stage / "Install-KoreanPatch.ps1"
            installer.write_text(render_profile_installer(installer.read_text(encoding="utf-8-sig"), profile_spec), encoding="utf-8-sig")
            readme = stage / "README.ko.md"
            readme.write_text(
                "# Cavalry Girls 한국어 패치\n\n"
                f"대상 게임 버전: {profile_spec.version} / Steam 빌드 {profile_spec.raw.get('steam_build_id')} / r06\n\n"
                "게임 업데이트와 한글패치 적용 후의 화면 확인을 위한 테스트 후보입니다. 미출시 DLC의 포함된 텍스트도 번역 대상에 포함합니다.\n\n"
                "1. 게임을 종료하세요. 기존 패치가 남아 있다면 Steam 파일 무결성 검사로 최신 원본 파일을 준비하세요.\n"
                "2. 기존 `.cavalry-girls-ko-backup` 폴더가 있다면 다른 이름으로 보관하세요. 이전 버전의 백업을 최신 게임에 복원하지 마세요.\n"
                "3. ZIP 전체를 게임 폴더 안의 하위 폴더 하나에 풀어 주세요. `CavalryGirls.exe`와 패치 폴더가 같은 위치에 있어야 합니다.\n"
                "4. 패치 폴더의 `install.cmd`를 실행하세요. 설치 대상은 패치 폴더의 바로 위 게임 폴더입니다.\n"
                "5. 게임 언어에서 한국어를 선택하세요. 이 패치는 일본어 언어 슬롯을 사용합니다.\n\n"
                "원복은 게임을 종료한 뒤 같은 패치 폴더의 `restore.cmd`를 실행하세요. 새 게임 업데이트 뒤에는 기존 백업을 복원하지 말고 Steam 무결성 검사를 이용하세요.\n\n"
                "다른 버전의 원본 파일이면 설치가 중단됩니다. 게임 실행·화면·저장/불러오기 확인은 아직 남아 있습니다. `MANUAL-TEST.ko.md`를 확인하세요.\n",
                encoding="utf-8-sig",
            )
            manual = stage / "MANUAL-TEST.ko.md"
            manual.write_text(
                "# 실기 확인표\n\n"
                f"프로필 버전 {profile_spec.version}을 기준으로 다음을 확인하세요.\n\n"
                "- 타이틀 이미지와 한국어 글꼴이 정상 표시되는지 확인.\n"
                "- 2560×1440에서 보관함·상점·상세창 본문·업그레이드 목록의 잘림과 행간을 확인.\n"
                "- 재주조·복제의 빈 슬롯 초기 안내 및 아이템을 넣은 뒤 동적 표시를 확인.\n"
                "- 자동 조립·자동 선물·제작 검색과 필터·전투 HUD의 새 문구를 확인.\n"
                "- 접근 가능한 새 DLC 대사·장비·지도 설명의 줄바꿈을 확인. 미출시 부분은 실제 접근 후 별도 확인.\n"
                "- 저장/불러오기와 복원 스크립트를 확인.\n",
                encoding="utf-8-sig",
            )
        if manifest["patch_revision"] in UI_RECIPES:
            readme = stage / "README.ko.md"
            text = readme.read_text(encoding="utf-8-sig")
            text = text.replace(
                "r03는 전투 좌하단 고정 안내를 한국어로 추가 번역하고 해당 Text의 글꼴을 한국어 Font로 연결했습니다. r02의 한국어 언어 버튼과 로고 표시 영역 수정도 포함합니다.",
                "r04는 아이템 상세창의 행간·여백을 줄이고 업그레이드 모델 안내와 재주조·복제 초기 안내 네 곳을 한국어로 수정합니다. r03까지의 번역·폰트·타이틀 수정도 포함합니다.",
            )
            text = text.replace("기존 r01/r02 패치", "기존 r01/r02/r03 패치").replace(".cavalry-girls-ko-backup-r02", ".cavalry-girls-ko-backup-r03").replace("r03용 새 백업", "r04용 새 백업")
            text = text.replace("이 패치는 기존 8,773개와 새 고정 UI 1개를 합한 8,774개 레코드의 QA·L1·L2 검수와 1,979개 전역 표적 검수를 완료했습니다.", "기존 승인 8,774개에 새 고정 UI 4개를 더한 총 8,778개 레코드입니다. 새 네 문구의 QA·L1·L2 검수를 완료했으며, 새 상세창 간격과 초기/동적 표시의 실기 확인은 남아 있습니다.")
            text = text.replace("합한 8,774개 레코드,", "합한 기존 8,774개와 r04 고정 UI 4개를 포함한 총 8,778개 레코드,")
            if manifest["patch_revision"] == "r05":
                text = text.replace("CavalryGirls-Korean-r04", "CavalryGirls-Korean-r05").replace(".cavalry-girls-ko-backup-r03", ".cavalry-girls-ko-backup-r04")
                text = text.replace("r04는 아이템 상세창의 행간·여백을 줄이고 업그레이드 모델 안내와 재주조·복제 초기 안내 네 곳을 한국어로 수정합니다. r03까지의 번역·폰트·타이틀 수정도 포함합니다.", "r05는 상세창 본문 행간을 화면 기준 약 1.125배로 맞추는 값을 적용합니다. 실제 표시 확인이 필요한 후보이며 r04의 번역·폰트·타이틀 및 설치 방식도 포함합니다.")
            readme.write_text(text, encoding="utf-8-sig")
            manual = stage / "MANUAL-TEST.ko.md"
            text = manual.read_text(encoding="utf-8-sig")
            text = text.replace("# 실기 확인표\n", "# 실기 확인표\n\n먼저 r04 신고 화면을 2560×1440에서 확인하세요.\n\n- 보관함의 긴 무기 상세창: 제목·설명·수치·하단 업그레이드 목록이 화면 안에 들어오고 줄이 겹치지 않는지 확인.\n- 상점의 짧은 장비 상세창: 간격과 업그레이드 및 개조 모델 안내 확인.\n- 재주조 및 복제 화면: 빈 슬롯의 안내가 한국어인지, 아이템을 넣은 뒤 실제 수치와 문구가 정상 갱신되는지 확인.\n")
            manual.write_text(text, encoding="utf-8-sig")
        package_manifest = {
            "schema_version": "1.0.0", "source_revision": profile_spec.source_revision if profile_spec is not None else SOURCE_REVISION,
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
            "game_version": profile_spec.version if profile_spec is not None else "2.6.2859", "steam_build_id": (profile_spec.raw.get("steam_build_id") if profile_spec is not None else "24639430"), "app_id": (profile_spec.raw.get("app_id") if profile_spec is not None else "2055050"), "runtime_verifier": "human",
            "known_debt": ["Final full-build gameplay/display/save reload requires human validation.", "Opaque Enc/ArtBook and non-title image text remain outside scope."],
        }
        if manifest["patch_revision"] in UI_RECIPES or (
            profile_spec is not None and manifest.get("maintenance_ui") is not None
        ):
            if profile_spec is not None:
                package_manifest["maintenance_ui"] = manifest["maintenance_ui"]
                package_manifest["known_debt"].append(
                    "r06: verify approved static UI strings and layout changes in runtime."
                )
            else:
                package_manifest["maintenance_ui"] = manifest["maintenance_ui"]
                package_manifest["known_debt"].append("r04: verify long weapon tooltip height and upgrade/reforge/duplicate labels in initial and populated states.")
        if profile_spec is not None:
            package_manifest["profile"] = {
                "path": str(profile_spec.path.relative_to(ROOT)),
                "sha256": sha256_file(profile_spec.path),
                "source_hash_manifest_sha256": profile_spec.source_manifest_sha256,
                "coverage": profile_spec.coverage,
                "artifact_digests": {key: value["sha256"] for key, value in profile_spec.artifacts.items()},
                "dependency_hashes": profile_dependency_hashes,
            }
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
    parser.add_argument("--profile", type=Path, default=None, help="validated version profile (requires explicit --source-game)")
    args = parser.parse_args()
    source_game_arg = any(arg == "--source-game" or arg.startswith("--source-game=") for arg in sys.argv[1:])
    if args.profile is not None and not source_game_arg:
        parser.error("--profile requires explicit --source-game")
    try:
        result = package(args.build, args.source_game, args.output, profile=args.profile)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, "output": result["output"], "targets": 2, "runtime_verified": False}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
