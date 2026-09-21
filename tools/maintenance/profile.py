#!/usr/bin/env python3
"""Validation and normalization for versioned maintenance build profiles.

Profiles are data.  They may name files and hashes, but they never name a
Python module, command, or callable.  Keeping the validation here gives the
build and package entry points one small, deterministic interface while the
legacy r03/r04/r05 constants remain untouched.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[2]
PROFILE_VERSION = "3.0.2950"
PROFILE_SOURCE_REVISION = "f341ed52b0f2a51c24488de4f7b4bf32404c943244fe0263dde3f5d36a1f15ab"
REQUIRED_SOURCE_PATHS = (
    "CavalryGirls_Data/resources.assets",
    "CavalryGirls_Data/resources.assets.resS",
    "AssetBundles/fonts_assets_all.bundle",
)
REQUIRED_PROFILE_DEPENDENCIES = frozenset(
    {
        "tools/l10n.py",
        "tools/maintenance/build.py",
        "tools/maintenance/profile.py",
        "tools/maintenance/package.py",
        "adapters/cavalry_girls.py",
        "adapters/table_dialect.py",
        "adapters/font_adapter.py",
        "adapters/title_sprite.py",
        "adapters/hud_adapter.py",
        "adapters/ui_maintenance.py",
    }
)


class ProfileError(ValueError):
    """A profile is missing a binding or contains an unsafe binding."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _project_path(root: Path, value: str | Path, label: str) -> Path:
    if not isinstance(value, (str, Path)) or not str(value):
        raise ProfileError(f"{label} must be a non-empty project-relative path")
    candidate = Path(value)
    if candidate.is_absolute():
        raise ProfileError(f"{label} must not be an absolute path")
    resolved = (root / candidate).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ProfileError(f"{label} escapes project root")
    return resolved


def _path_value(spec: Any, label: str) -> str | None:
    if isinstance(spec, str):
        return spec
    if isinstance(spec, Mapping):
        value = spec.get("path") or spec.get("file")
        return value if isinstance(value, str) else None
    return None


def _digest_value(spec: Any) -> str | None:
    if isinstance(spec, str) and len(spec) == 64:
        return spec
    if isinstance(spec, Mapping):
        for key in ("sha256", "digest", "file_sha256", "profile_sha256"):
            value = spec.get(key)
            if isinstance(value, str) and len(value) == 64:
                return value
    return None


def _valid_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _game_relative_path(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ProfileError(f"{label} must be a non-empty POSIX relative path")
    path = Path(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ProfileError(f"{label} is unsafe")
    normalized = path.as_posix()
    if normalized != value:
        raise ProfileError(f"{label} is not normalized")
    return normalized


def _file_binding(root: Path, spec: Any, label: str, *, required: bool = True) -> dict[str, Any] | None:
    path_value = _path_value(spec, label)
    if path_value is None:
        if required:
            raise ProfileError(f"{label} has no path")
        return None
    path = _project_path(root, path_value, label)
    declared = _digest_value(spec)
    if declared is None:
        if required:
            raise ProfileError(f"{label} has no sha256 binding")
        return {"path": path, "sha256": None}
    if not path.is_file():
        raise ProfileError(f"{label} is missing: {path.relative_to(root)}")
    observed = sha256_file(path)
    if observed != declared:
        raise ProfileError(f"{label} sha256 mismatch: {path.relative_to(root)}")
    return {"path": path, "sha256": declared}


def _first(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping:
            return mapping[key]
    return None


def _target_ids(raw: Mapping[str, Any]) -> tuple[int, ...]:
    values = _first(raw, "text_target_ids", "target_text_path_ids")
    text = raw.get("text_assets")
    if values is None and isinstance(text, list):
        values = [item.get("path_id") for item in text if isinstance(item, Mapping)]
    targets = raw.get("targets")
    if values is None and isinstance(targets, Mapping):
        values = _first(targets, "text_path_ids", "text_asset_path_ids")
    if not isinstance(values, list) or not values:
        raise ProfileError("profile must pin at least one TextAsset path id")
    try:
        result = tuple(sorted({int(value) for value in values}))
    except (TypeError, ValueError) as exc:
        raise ProfileError("profile TextAsset path ids must be integers") from exc
    if any(value <= 0 for value in result) or len(result) != len(values):
        raise ProfileError("profile TextAsset path ids must be unique positive integers")
    return result


def _title(raw: Mapping[str, Any], root: Path = ROOT) -> dict[str, Any]:
    title = raw.get("title")
    if not isinstance(title, Mapping):
        title = raw.get("external_title")
    if not isinstance(title, Mapping):
        raise ProfileError("profile must pin title offset and size")
    try:
        offset = int(title.get("offset", title.get("texture_stream_offset")))
        size = int(title.get("size", title.get("texture_stream_size")))
    except (KeyError, TypeError, ValueError) as exc:
        raise ProfileError("title offset and size are required integers") from exc
    if offset < 0 or size <= 0:
        raise ProfileError("title offset and size must be non-negative/positive")
    result = {"offset": offset, "size": size}
    for key, aliases in {
        "payload_sha256": ("payload_sha256",),
        "original_sha256": ("original_sha256", "original_span_sha256", "texture_stream_source_sha256"),
        "source_sha256": ("source_sha256",),
    }.items():
        value = next((title.get(alias) for alias in aliases if title.get(alias) is not None), None)
        if value is not None:
            if not isinstance(value, str) or len(value) != 64:
                raise ProfileError(f"title {key} must be a sha256 string")
            result[key] = value
    payload_spec = title.get("payload") or title.get("payload_path")
    payload = _path_value(payload_spec, "title.payload")
    if payload is not None:
        binding = _file_binding(root, payload_spec, "title.payload")
        result["payload"] = binding
    return result


def _record_count(raw: Mapping[str, Any]) -> int:
    counts = raw.get("counts")
    corpus = raw.get("corpus")
    snapshot = raw.get("translation_snapshot")
    candidates = [
        raw.get("record_count"),
        raw.get("localized_record_count"),
        counts.get("record_count") if isinstance(counts, Mapping) else None,
        counts.get("localized") if isinstance(counts, Mapping) else None,
        corpus.get("record_count") if isinstance(corpus, Mapping) else None,
        snapshot.get("record_count") if isinstance(snapshot, Mapping) else None,
    ]
    for value in candidates:
        if isinstance(value, int) and value > 0:
            return value
    raise ProfileError("profile must pin a positive localized record count")


def _coverage(raw: Mapping[str, Any]) -> dict[str, Any]:
    value = raw.get("coverage") or raw.get("review_coverage")
    if not isinstance(value, Mapping):
        raise ProfileError("profile must contain current QA/L1/L2 coverage")
    required = {"qa", "l1", "l2"}
    aliases = {"qa": ("qa", "QA"), "l1": ("l1", "L1"), "l2": ("l2", "L2")}
    result: dict[str, Any] = {}
    for stage, keys in aliases.items():
        found = next((value[key] for key in keys if key in value), None)
        if found is None:
            raise ProfileError(f"profile coverage is missing {stage}")
        if isinstance(found, Mapping):
            ok = found.get("complete") is True or found.get("ok") is True or found.get("covered") is True
        else:
            ok = found is True
        if not ok:
            raise ProfileError(f"profile coverage is not complete: {stage}")
        result[stage] = found
    blockers = raw.get("blockers")
    if blockers is None and isinstance(raw.get("issues"), Mapping):
        blockers = raw["issues"].get("blockers")
    if blockers not in (None, [], {}):
        raise ProfileError("profile contains unresolved blockers")
    result["blockers"] = []
    return result


def _artifact_bindings(root: Path, raw: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    source = raw.get("digests") or raw.get("integrity") or raw.get("artifacts")
    if source is None:
        raise ProfileError("profile must pin corpus/map/snapshot/review/profile digests")
    if not isinstance(source, Mapping):
        raise ProfileError("profile digests must be an object")
    aliases = {
        "corpus": ("corpus", "corpus_digest"),
        "maps": ("maps", "map", "patch_map", "maps_digest"),
        "snapshot": ("snapshot", "translation_snapshot", "snapshot_digest"),
        "reviews": ("reviews", "review", "review_digest"),
        "profile": ("profile", "profile_digest"),
    }
    for name, keys in aliases.items():
        spec = next((source[key] for key in keys if key in source), None)
        if spec is None:
            raise ProfileError(f"profile digest binding is missing: {name}")
        binding = _file_binding(root, spec, f"{name} digest")
        if binding is None:
            raise ProfileError(f"profile digest binding is missing: {name}")
        result[name] = binding
    return result


@dataclass(frozen=True)
class BuildProfile:
    """Normalized, hash-bound data consumed by build/package."""

    path: Path
    version: str
    source_revision: str
    source_manifest: Path
    source_manifest_sha256: str
    source_hashes: dict[str, str]
    text_target_ids: tuple[int, ...]
    record_count: int
    title: dict[str, Any]
    coverage: dict[str, Any]
    artifacts: dict[str, dict[str, Any]]
    raw: dict[str, Any]

    def source_path(self, relative: str) -> str:
        if relative not in self.source_hashes:
            raise ProfileError(f"profile source hash is missing: {relative}")
        return self.source_hashes[relative]


def load_profile(path: Path, *, root: Path = ROOT) -> BuildProfile:
    """Load and validate a 3.0.2950 profile without executing profile data."""
    path = path.resolve()
    if not path.is_file() or not path.is_relative_to(root.resolve()):
        raise ProfileError("profile must be an existing file under the project root")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProfileError(f"cannot read profile: {path}") from exc
    if not isinstance(raw, dict):
        raise ProfileError("profile root must be a JSON object")
    version = _first(raw, "game_version", "version")
    game = raw.get("game")
    if version is None and isinstance(game, Mapping):
        version = game.get("version")
    if version != PROFILE_VERSION:
        raise ProfileError(f"profile game version must be {PROFILE_VERSION}")
    source_revision = raw.get("source_revision")
    if source_revision != PROFILE_SOURCE_REVISION:
        raise ProfileError("profile source revision mismatch")

    manifest_spec = _first(raw, "source_hash_manifest", "source_manifest")
    if not isinstance(manifest_spec, Mapping):
        raise ProfileError("profile must explicitly bind source_hash_manifest path and sha256")
    manifest_path_value = _path_value(manifest_spec, "source hash manifest")
    if manifest_path_value is None:
        raise ProfileError("source hash manifest has no path")
    manifest_path = _project_path(root, manifest_path_value, "source hash manifest")
    if not manifest_path.is_file():
        raise ProfileError(f"source hash manifest is missing: {manifest_path}")
    manifest_digest = _digest_value(manifest_spec)
    if not _valid_sha256(manifest_digest):
        raise ProfileError("source hash manifest must have an explicit lowercase sha256")
    if sha256_file(manifest_path) != manifest_digest:
        raise ProfileError("source hash manifest digest mismatch")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("source_revision") != source_revision or not isinstance(manifest.get("files"), list):
        raise ProfileError("source hash manifest revision/files mismatch")
    source_hashes: dict[str, str] = {}
    for item in manifest["files"]:
        if not isinstance(item, Mapping):
            raise ProfileError("source hash manifest has malformed file entry")
        relative = _game_relative_path(item.get("path"), "source hash manifest path")
        digest = item.get("sha256")
        if not _valid_sha256(digest):
            raise ProfileError("source hash manifest has malformed sha256")
        if relative in source_hashes:
            raise ProfileError(f"source hash manifest has duplicate path: {relative}")
        source_hashes[relative] = digest
    if any(relative not in source_hashes for relative in REQUIRED_SOURCE_PATHS):
        raise ProfileError("source hash manifest lacks required build inputs")
    profile_hashes = raw.get("bound_source_hashes")
    if isinstance(profile_hashes, list):
        profile_hashes = {item.get("path"): item.get("sha256") for item in profile_hashes if isinstance(item, Mapping)}
    if not isinstance(profile_hashes, Mapping) or set(profile_hashes) != set(REQUIRED_SOURCE_PATHS):
        raise ProfileError("bound_source_hashes must pin exactly the required build inputs")
    for relative in REQUIRED_SOURCE_PATHS:
        expected = profile_hashes[relative]
        if not _valid_sha256(expected) or expected != source_hashes[relative]:
            raise ProfileError(f"profile source hash is not bound to manifest: {relative}")
    artifacts = _artifact_bindings(root, raw)
    coverage = _coverage(raw)
    normalized = BuildProfile(
        path=path,
        version=version,
        source_revision=source_revision,
        source_manifest=manifest_path,
        source_manifest_sha256=manifest_digest,
        source_hashes=source_hashes,
        text_target_ids=_target_ids(raw),
        record_count=_record_count(raw),
        title=_title(raw, root),
        coverage=coverage,
        artifacts=artifacts,
        raw=raw,
    )
    return normalized


def validate_source_game(profile: BuildProfile, source_game: Path) -> dict[str, str]:
    """Verify the profile-bound build inputs in a read-only source directory."""
    source_game = source_game.resolve()
    if not source_game.is_dir():
        raise ProfileError("source-game must be an existing directory")
    observed: dict[str, str] = {}
    for relative in REQUIRED_SOURCE_PATHS:
        expected = profile.source_hashes[relative]
        source = (source_game / relative).resolve()
        if (
            not source.is_relative_to(source_game)
            or not source.is_file()
            or sha256_file(source) != expected
        ):
            raise ProfileError(f"pristine source hash mismatch: {relative}")
        observed[relative] = expected
    return observed


def verify_profile_dependencies(profile: BuildProfile, *, root: Path = ROOT) -> dict[str, str]:
    """Verify the exact code dependency set supplied by the version profile."""
    raw = profile.raw.get("dependencies") or profile.raw.get("adapter_dependencies")
    if isinstance(raw, list):
        raw = {item.get("path"): item.get("sha256") for item in raw if isinstance(item, Mapping)}
    if not isinstance(raw, Mapping):
        raise ProfileError("profile dependencies must be an object or list")
    if set(raw) != REQUIRED_PROFILE_DEPENDENCIES:
        missing = sorted(REQUIRED_PROFILE_DEPENDENCIES - set(raw))
        extra = sorted(set(raw) - REQUIRED_PROFILE_DEPENDENCIES)
        raise ProfileError(f"profile dependency set mismatch: missing={missing}, extra={extra}")
    observed: dict[str, str] = {}
    for relative, expected in raw.items():
        if not isinstance(relative, str) or not _valid_sha256(expected):
            raise ProfileError("profile dependency binding is malformed")
        path = _project_path(root, relative, "profile dependency")
        if not path.is_file() or sha256_file(path) != expected:
            raise ProfileError(f"profile dependency hash mismatch: {relative}")
        observed[relative] = expected
    return observed


def verify_profile_approvals(profile: BuildProfile, *, root: Path = ROOT) -> dict[str, Any]:
    """Recompute coverage from exact bound source, translation and review records."""
    spec = importlib.util.spec_from_file_location("cg_profile_l10n", root / "tools/l10n.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sources = module.read_rows(profile.artifacts["corpus"]["path"])
    translations = module.read_rows(profile.artifacts["snapshot"]["path"])
    reviews = module.read_rows(profile.artifacts["reviews"]["path"])
    if len(sources) != profile.record_count or len(translations) != profile.record_count:
        raise ProfileError("profile approval corpus/snapshot count mismatch")
    if any(row["source_revision"] != profile.source_revision for row in sources):
        raise ProfileError("profile approval corpus revision mismatch")
    if any(row.get("status") != "release_approved" for row in translations):
        raise ProfileError("profile snapshot is not fully reviewed")
    report = module.qa(root, sources, translations, reviews, complete=True, require_stage="l2")
    if not report["ok"]:
        raise ProfileError("profile exact QA/L1/L2 coverage failed: " + json.dumps(report["errors"], ensure_ascii=False))
    # Operational inputs must be pinned too; otherwise an unrelated approved
    # artifact could be paired with different files passed to the builder.
    paths = profile_paths(profile, root=root)
    specs = profile_path_specs(profile.raw)
    for key, path in paths.items():
        binding = _file_binding(root, specs[key], key)
        if binding is None or binding["path"] != path:
            raise ProfileError(f"operational binding differs after resolution: {key}")
    if paths.get("translations") != profile.artifacts["snapshot"]["path"]:
        raise ProfileError("operational translations differ from reviewed snapshot")
    if paths.get("patch_map") != profile.artifacts["maps"]["path"]:
        raise ProfileError("operational patch-map differs from reviewed maps")
    source_index = module.unique_index(sources)
    operational = []
    for key in ("segments", "ui_source", "ui_static_source"):
        if key in paths:
            operational += module.read_rows(paths[key])
    if module.unique_index(operational) != source_index:
        raise ProfileError("operational source differs from reviewed corpus")
    return report


def profile_path_specs(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Return operational binding specs regardless of their supported container."""
    result: dict[str, Any] = {}
    nested: dict[str, Any] = {}
    for container_name in ("corpus", "inputs", "artifacts"):
        container = raw.get(container_name)
        if isinstance(container, Mapping):
            nested.update(container)
    for key, aliases in {
        "translations": ("translations", "translation_snapshot"),
        "segments": ("segments",),
        "patch_map": ("patch_map", "maps"),
        "ui_source": ("ui_source",),
        "ui_patch_map": ("ui_patch_map",),
        "ui_static_source": ("ui_static_source",),
        "ui_static_patch_map": ("ui_static_patch_map",),
        "asset_profile": ("asset_profile",),
        "ui_recipe": ("ui_recipe", "recipe"),
    }.items():
        value = next((raw[name] for name in aliases if name in raw), None)
        if value is None:
            value = next((nested[name] for name in aliases if name in nested), None)
        if _path_value(value, key) is not None:
            result[key] = value
    return result


def profile_paths(profile: BuildProfile, *, root: Path = ROOT) -> dict[str, Path]:
    """Return operational data paths after applying project-root path checks."""
    return {
        key: _project_path(root, _path_value(value, key), key)
        for key, value in profile_path_specs(profile.raw).items()
    }


__all__ = [
    "BuildProfile",
    "PROFILE_SOURCE_REVISION",
    "PROFILE_VERSION",
    "ProfileError",
    "REQUIRED_PROFILE_DEPENDENCIES",
    "REQUIRED_SOURCE_PATHS",
    "load_profile",
    "profile_paths",
    "sha256_file",
    "validate_source_game",
    "verify_profile_dependencies",
]
