#!/usr/bin/env python3
"""Local, non-network localization data tools. Python >=3.11; see tools/README.md."""
from __future__ import annotations

import argparse
import codecs
import hashlib
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

try:
    from jsonschema import Draft202012Validator
except ImportError:
    raise SystemExit("Missing dependency: python -m pip install -r requirements.txt")

ROOT = Path(__file__).resolve().parents[1]
VERSION = "1.0.0"
TRANSLATORS = {"translator", "translator_deep", "translator_senior"}
L1_ROLES = {"reviewer_l1", "reviewer_l1_astra"}


def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in pairs:
        if k in out:
            raise ValueError(f"Duplicate JSON key: {k}")
        out[k] = v
    return out


def parse(text: str) -> Any:
    return json.loads(text, object_pairs_hook=reject_duplicate_keys)


def read_json(path: Path) -> Any:
    return parse(path.read_text(encoding="utf-8"))


def read_rows(path: Path) -> list[dict[str, Any]]:
    if path.suffix == ".jsonl":
        rows = []
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                raise ValueError(f"{path}:{n}: blank JSONL record")
            try:
                row = parse(line)
            except (ValueError, json.JSONDecodeError) as exc:
                raise ValueError(f"{path}:{n}: {exc}") from exc
            rows.append(row)
        return rows
    data = read_json(path)
    return data if isinstance(data, list) else [data]


def sha_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_hash(data: Any) -> str:
    return sha_text(json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":")))


def safe(root: Path, relative: str) -> Path:
    """Reject traversal and symlink escape. This is not an OS sandbox."""
    if not relative or "\\" in relative or Path(relative).is_absolute() or re.match(r"^[A-Za-z]:", relative):
        raise ValueError(f"Expected a project-relative POSIX path: {relative!r}")
    if ".." in Path(relative).parts:
        raise ValueError(f"Parent traversal is not allowed: {relative}")
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or path == root.resolve():
        raise ValueError(f"Path escapes the project: {relative}")
    return path


def relative(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".tmp-{os.getpid()}")
    try:
        with temp.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def write_json(path: Path, data: Any) -> None:
    atomic_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def write_rows(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    atomic_text(path, "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))


def schema_errors(root: Path, name: str, rows: list[Any]) -> list[str]:
    schema = read_json(root / "schemas" / f"{name}.schema.json")
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    errors = []
    for n, row in enumerate(rows, 1):
        for error in sorted(validator.iter_errors(row), key=lambda e: str(list(e.path))):
            errors.append(f"{name}[{n}]/{'/'.join(map(str, error.path))}: {error.message}")
    return errors


def enforce_schema(root: Path, name: str, rows: list[Any]) -> None:
    errors = schema_errors(root, name, rows)
    if errors:
        raise ValueError("\n".join(errors[:20]))


def unique_index(rows: list[dict[str, Any]], key: str = "id") -> dict[str, dict[str, Any]]:
    out = {}
    for row in rows:
        value = row[key]
        if value in out:
            raise ValueError(f"Duplicate {key}: {value}")
        out[value] = row
    return out


def tokens(text: str, constraints: dict[str, Any]) -> list[str]:
    patterns = constraints["token_patterns"]
    if patterns:
        regex = re.compile("|".join(f"(?:{p})" for p in patterns))
    elif constraints["protected_tokens"]:
        literals = sorted(set(constraints["protected_tokens"]), key=lambda x: (-len(x), x))
        if any(not value for value in literals):
            raise ValueError("Empty protected token")
        regex = re.compile("|".join(re.escape(value) for value in literals))
    else:
        return []
    matches = list(regex.finditer(text))
    if any(match.start() == match.end() for match in matches):
        raise ValueError("A token pattern matches an empty string")
    return [match.group(0) for match in matches]


def qa(root: Path, sources: list[dict[str, Any]], translations: list[dict[str, Any]],
       reviews: list[dict[str, Any]] | None = None, complete: bool = False,
       require_stage: str | None = None) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    def error(code: str, detail: str, sid: str | None = None) -> None:
        errors.append({"id": sid, "check": code, "detail": detail})

    def warning(code: str, detail: str, sid: str | None = None) -> None:
        warnings.append({"id": sid, "check": code, "detail": detail})

    def result() -> dict[str, Any]:
        return {"schema_version": VERSION, "ok": not errors, "errors": errors,
                "warnings": warnings, "counts": {"source": len(sources), "translations": len(translations),
                "reviews": len(reviews or [])}, "limits": "No semantic, full engine-grammar, glyph, or runtime validation."}

    for name, rows in [("segment", sources), ("translation", translations), ("review", reviews or [])]:
        for detail in schema_errors(root, name, rows):
            error("schema", detail)
    if errors:
        return result()
    if not sources:
        error("empty_source", "A nonempty source corpus is required")
    if not translations:
        error("empty_translation", "A nonempty translation candidate is required")
    try:
        src = unique_index(sources)
        tr = unique_index(translations)
        unique_index(reviews or [], "review_id")
    except ValueError as exc:
        error("duplicate_id", str(exc))
        return result()
    locations: set[tuple[str, str, str]] = set()
    for source in sources:
        sid = source["id"]
        if sha_text(source["source"]["text"]) != source["source"]["text_sha256"]:
            error("source_hash", "Source logical-text hash mismatch", sid)
        loc = source["source"]["locator"]
        key = (source["source"]["file"], loc["kind"], loc["value"])
        if key in locations:
            error("duplicate_locator", "Multiple IDs target the same source location", sid)
        locations.add(key)
        try:
            if tokens(source["source"]["text"], source["constraints"]) != source["constraints"]["protected_tokens"]:
                error("source_tokens", "Declared tokens do not match the extracted source sequence", sid)
        except (ValueError, re.error) as exc:
            error("token_profile", str(exc), sid)
    if complete:
        for sid in sorted(src.keys() - tr.keys()):
            error("missing_id", "No translation for a required source ID", sid)
    for sid, row in tr.items():
        if sid not in src:
            error("unknown_id", "Translation ID is not in source", sid)
            continue
        source = src[sid]
        if row["source_revision"] != source["source_revision"] or row["source_sha256"] != source["source"]["text_sha256"]:
            error("stale_source", "Translation targets another source revision/hash", sid)
        text = row["ko"]
        if row["status"] == "blocked" or text is None:
            error("blocked_translation", "Translation is blocked or null", sid)
            continue
        c = source["constraints"]
        original = source["source"]["text"]
        if not text and not c["allow_empty"]:
            error("empty_target", "An empty target is not allowed", sid)
        try:
            observed = tokens(text, c)
            expected = c["protected_tokens"]
            mismatch = observed != expected if c["token_order"] == "ordered" else Counter(observed) != Counter(expected)
            if mismatch:
                error("protected_tokens", "Protected token count/order differs", sid)
        except (ValueError, re.error) as exc:
            error("token_profile", str(exc), sid)
        if c["newline_policy"] == "preserve" and re.findall(r"\r\n|\r|\n", text) != re.findall(r"\r\n|\r|\n", original):
            error("newlines", "Literal newline sequence differs", sid)
        if c["max_codepoints"] is not None and len(text) > c["max_codepoints"]:
            error("max_codepoints", "Target exceeds the registered codepoint limit", sid)
        if c["max_lines"] is not None and len(re.split(r"\r\n|\r|\n", text)) > c["max_lines"]:
            error("max_lines", "Target exceeds the registered line limit", sid)
        if c["max_bytes"] is not None and c["encoding"] is None:
            error("unknown_encoding", "max_bytes requires a known encoding", sid)
        if c["encoding"] is not None:
            try:
                codecs.lookup(c["encoding"])
                encoded = text.encode(c["encoding"], errors="strict")
                if c["max_bytes"] is not None and len(encoded) > c["max_bytes"]:
                    error("max_bytes", "Target exceeds encoded byte limit", sid)
            except (LookupError, UnicodeError) as exc:
                error("encoding", str(exc), sid)
        if text and text == original:
            warning("unchanged", "Target equals source; review intentional retention", sid)

    # Review decisions are tied to the exact source, context and translation revision.
    if require_stage:
        stages = ["l1"] if require_stage == "l1" else ["l1", "l2"]
        for sid, row in tr.items():
            if sid not in src or row["ko"] is None:
                continue
            for stage in stages:
                candidates = [r for r in (reviews or []) if r["id"] == sid and r["stage"] == stage]
                matching = [r for r in candidates if (
                    r["source_revision"] == row["source_revision"]
                    and r["source_sha256"] == row["source_sha256"]
                    and r["context_pack_id"] == row["context_pack_id"]
                    and r["context_pack_digest"] == row["context_pack_digest"]
                    and r["reviewed_translation_revision"] == row["translation_revision"]
                    and r["reviewed_translation_sha256"] == sha_text(row["ko"]))]
                if not matching:
                    error("missing_review", f"No current {stage} review for this exact candidate", sid)
                    continue
                if any(r["verdict"] != "accept" for r in matching):
                    error("review_not_accepted", f"A current {stage} decision is pending/blocked/changes_required", sid)
                    continue
                areas = set().union(*(set(r["reviewed_areas"]) for r in matching))
                if not {"meaning", "naturalness"}.issubset(areas):
                    error("review_coverage", f"Current {stage} lacks meaning/naturalness coverage", sid)
    return result()


def file_entry(root: Path, path: Path, purpose: str) -> dict[str, str]:
    if not path.is_file():
        raise ValueError(f"Required file missing: {path}")
    return {"path": relative(root, path), "sha256": sha_file(path), "purpose": purpose}


def verify_pack(root: Path, manifest_path: Path) -> dict[str, Any]:
    pack = read_json(manifest_path)
    enforce_schema(root, "context-pack", [pack])
    unhashed = {key: value for key, value in pack.items() if key != "digest"}
    if canonical_hash(unhashed) != pack["digest"]:
        raise ValueError("Context pack manifest digest mismatch")
    unique_index(pack["files"], "path")
    for item in pack["files"]:
        path = safe(root, item["path"])
        if not path.is_file() or sha_file(path) != item["sha256"]:
            raise ValueError(f"Context file is missing or changed: {item['path']}")
    return pack


def make_pack(root: Path, corpus_path: Path, selection_path: Path,
              knowledge_paths: list[Path], out: Path) -> dict[str, Any]:
    selection = read_json(selection_path)
    enforce_schema(root, "selection", [selection])
    sources = read_rows(corpus_path)
    enforce_schema(root, "segment", sources)
    src = unique_index(sources)
    ids = selection["segment_ids"]
    nearby = selection["nearby_segment_ids"]
    if set(ids) & set(nearby):
        raise ValueError("Target and read-only nearby IDs must be disjoint")
    selected = []
    adjacent = []
    for sid in ids + nearby:
        if sid not in src:
            raise ValueError(f"Unknown selected source ID: {sid}")
        row = src[sid]
        if row["source_revision"] != selection["source_revision"]:
            raise ValueError(f"Source revision differs: {sid}")
        if sha_text(row["source"]["text"]) != row["source"]["text_sha256"]:
            raise ValueError(f"Source hash differs: {sid}")
        for field in ("route_id", "phase_id", "scene_id") if sid in ids else ("route_id", "phase_id"):
            if row["context"][field] != selection[field]:
                raise ValueError(f"Cross-scope {field} in selection: {sid}")
        (selected if sid in ids else adjacent).append(row)
    knowledge = [row for path in knowledge_paths for row in read_rows(path)]
    enforce_schema(root, "knowledge", knowledge)
    index = unique_index(knowledge)
    chosen = []
    for kid in selection["knowledge_ids"]:
        if kid not in index:
            raise ValueError(f"Missing knowledge ID: {kid}")
        item = index[kid]
        if item["status"] != "approved":
            raise ValueError(f"Unapproved knowledge cannot enter a pack: {kid}")
        scope = item["scope"]
        if scope["route_ids"] and selection["route_id"] not in scope["route_ids"]:
            raise ValueError(f"Wrong-route knowledge: {kid}")
        if scope["phase_ids"] and selection["phase_id"] not in scope["phase_ids"]:
            raise ValueError(f"Wrong-phase knowledge: {kid}")
        if scope["segment_ids"] and not (set(scope["segment_ids"]) & set(ids + nearby)):
            raise ValueError(f"Unrelated segment-scoped knowledge: {kid}")
        chosen.append(item)
    style = safe(root, selection["style_path"]).read_text(encoding="utf-8")
    source_text = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in selected)
    nearby_text = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in adjacent)
    knowledge_text = json.dumps(chosen, ensure_ascii=False, indent=2) + "\n"
    count = sum(map(len, [source_text, nearby_text, knowledge_text, style]))
    if count > selection["max_context_chars"]:
        raise ValueError(f"Context budget exceeded: {count} > {selection['max_context_chars']} characters")
    if out.exists():
        raise ValueError("Context pack is immutable: choose a new output directory")
    if not relative(root, out).startswith("work/"):
        raise ValueError("Generated packs must be under work/")
    out.mkdir(parents=True)
    for name, text in [("source.jsonl", source_text), ("nearby.jsonl", nearby_text), ("knowledge.json", knowledge_text), ("style.md", style)]:
        atomic_text(out / name, text)
    entries = [file_entry(root, out / name, purpose) for name, purpose in [
        ("source.jsonl", "target_source"), ("nearby.jsonl", "read_only_nearby"),
        ("knowledge.json", "scoped_approved_knowledge"), ("style.md", "translation_style")]]
    pack = {"schema_version": VERSION,
            "pack_id": selection["batch_id"] + ":context-" + canonical_hash([e["sha256"] for e in entries])[:12],
            "source_revision": selection["source_revision"], "batch_id": selection["batch_id"],
            "route_id": selection["route_id"], "phase_id": selection["phase_id"], "scene_id": selection["scene_id"],
            "segment_ids": ids, "nearby_segment_ids": nearby, "knowledge_ids": selection["knowledge_ids"],
            "files": entries, "context_chars": count}
    pack["digest"] = canonical_hash(pack)
    enforce_schema(root, "context-pack", [pack])
    write_json(out / "manifest.json", pack)
    return pack


def make_task(root: Path, role: str, pack_path: Path, task_id: str, attempt_id: str,
              out: Path, draft: Path | None = None, qa_path: Path | None = None,
              extra_inputs: list[Path] | None = None) -> dict[str, Any]:
    if role not in TRANSLATORS | L1_ROLES | {"reviewer_l2"}:
        raise ValueError("Automatic task generation supports translation/L1/L2. Use the template for other roles.")
    pack = verify_pack(root, pack_path)
    required = list(pack["files"])
    required.append(file_entry(root, pack_path, "fixed_context_manifest"))
    required.append(file_entry(root, root / "docs" / "roles" / f"{role}.md", "role_contract"))
    for name in ["translation" if role in TRANSLATORS else "review", "issue", "proposal", "task-summary"]:
        required.append(file_entry(root, root / "schemas" / f"{name}.schema.json", "output_contract"))
    if role not in TRANSLATORS:
        if draft is None or qa_path is None:
            raise ValueError("Review tasks require --draft and --qa-report")
        report = read_json(qa_path)
        if not report.get("ok"):
            raise ValueError("Do not dispatch review before deterministic QA passes")
        rows = read_rows(draft)
        source = read_rows(safe(root, next(f["path"] for f in pack["files"] if f["purpose"] == "target_source")))
        current = qa(root, source, rows, complete=True)
        if not current["ok"]:
            raise ValueError("Current draft fails QA: " + json.dumps(current["errors"], ensure_ascii=False))
        if any(r["context_pack_id"] != pack["pack_id"] or r["context_pack_digest"] != pack["digest"] for r in rows):
            raise ValueError("Draft targets another context pack")
        if role == "reviewer_l2" and any(r["status"] not in {"l1_approved", "release_approved"} for r in rows):
            raise ValueError("L2 expects the L1-approved integration snapshot")
        required += [file_entry(root, draft, "candidate_translation"), file_entry(root, qa_path, "deterministic_qa")]
    for path in extra_inputs or []:
        required.append(file_entry(root, path, "task_specific_input"))
    base = f"work/batches/{pack['batch_id']}/{attempt_id}/{role}"
    primary = "draft.jsonl" if role in TRANSLATORS else "review.jsonl"
    task = {"schema_version": VERSION, "task_id": task_id, "role": role, "batch_id": pack["batch_id"],
            "attempt_id": attempt_id, "path_base": "project_root", "context_pack_path": relative(root, pack_path),
            "context_pack_digest": pack["digest"], "required": required, "lookup_allowlist": [],
            "write_targets": [f"{base}/{name}" for name in [primary, "issues.jsonl", "proposals.jsonl", "summary.json"]],
            "max_repair_rounds": 2,
            "instructions": "배정된 ID만 처리한다. source/context revision을 유지한다. 출력 hash와 검수 판정을 조작하지 않는다."}
    enforce_schema(root, "task-manifest", [task])
    if out.exists():
        raise ValueError("Task manifest already exists; use a new task/attempt")
    if not relative(root, out).startswith("work/"):
        raise ValueError("Task manifests must be under work/")
    write_json(out, task)
    check_task(root, out)
    return task


def check_task(root: Path, manifest_path: Path) -> dict[str, Any]:
    task = read_json(manifest_path)
    enforce_schema(root, "task-manifest", [task])
    if not (root / ".codex" / "agents" / f"{task['role']}.toml").is_file():
        raise ValueError(f"Undefined role: {task['role']}")
    if not task["required"]:
        raise ValueError("Task must specify required input files")
    unique_index(task["required"], "path")
    role_path = f"docs/roles/{task['role']}.md"
    if role_path not in {f["path"] for f in task["required"]}:
        raise ValueError("Required role contract is missing")
    read_paths: set[Path] = set()
    for item in task["required"]:
        path = safe(root, item["path"])
        if not path.is_file() or sha_file(path) != item["sha256"]:
            raise ValueError(f"Required input missing or stale: {item['path']}")
        read_paths.add(path)
    for path in task["lookup_allowlist"]:
        safe(root, path)
    if task["context_pack_path"] is not None:
        pack = verify_pack(root, safe(root, task["context_pack_path"]))
        if pack["digest"] != task["context_pack_digest"]:
            raise ValueError("Task references another context digest")
    elif task["context_pack_digest"] is not None:
        raise ValueError("Context digest without a context path")
    allowed = ["work/"]
    if task["role"] == "corpus_builder":
        allowed += ["localization/corpus/"]
    elif task["role"] == "integrator":
        allowed += ["localization/knowledge/", "localization/translations/", "localization/tm/"]
    elif task["role"] == "technical_worker":
        allowed += ["tools/", "adapters/", "tests/"]
    resolved_targets: set[Path] = set()
    for value in task["write_targets"]:
        if any(c in value for c in "*?[]"):
            raise ValueError("write_targets must be exact file paths, not glob patterns")
        path = safe(root, value)
        if not any(relative(root, path).startswith(prefix) for prefix in allowed):
            raise ValueError(f"Role cannot write here: {value}")
        if path in read_paths or path in resolved_targets or path.is_dir():
            raise ValueError(f"Conflicting/duplicate write target: {value}")
        resolved_targets.add(path)
    return task


def lock_paths(root: Path, task: dict[str, Any]) -> list[Path]:
    return [root / "work" / "locks" / (sha_text(str(safe(root, target))) + ".lock") for target in task["write_targets"]]


def claim_task(root: Path, manifest_path: Path, owner: str) -> list[str]:
    if not owner.strip():
        raise ValueError("A nonempty owner is required")
    task = check_task(root, manifest_path)
    made = []
    try:
        for target, lock in zip(task["write_targets"], lock_paths(root, task)):
            lock.parent.mkdir(parents=True, exist_ok=True)
            with lock.open("x", encoding="utf-8") as stream:
                json.dump({"task_id": task["task_id"], "owner": owner, "target": target,
                           "created_at": datetime.now(timezone.utc).isoformat()}, stream, ensure_ascii=False)
            made.append(lock)
    except Exception:
        for path in made:
            path.unlink()
        raise
    return [relative(root, p) for p in made]


def release_task(root: Path, manifest_path: Path, owner: str) -> None:
    # Input hashes may have changed after a crash; validate the contract, not current inputs.
    task = read_json(manifest_path)
    enforce_schema(root, "task-manifest", [task])
    paths = lock_paths(root, task)
    for path in paths:
        lock = read_json(path)
        if lock["owner"] != owner or lock["task_id"] != task["task_id"]:
            raise ValueError("Cannot release another owner's/task's lock")
    for path in paths:
        path.unlink()


def prepare_review(root: Path, source: list[dict[str, Any]], translations: list[dict[str, Any]],
                   stage: str, role: str, prefix: str) -> list[dict[str, Any]]:
    report = qa(root, source, translations, complete=True)
    if not report["ok"]:
        raise ValueError("QA must pass before preparing review: " + json.dumps(report["errors"], ensure_ascii=False))
    rows = []
    for index, row in enumerate(translations, 1):
        rows.append({"schema_version": VERSION, "review_id": f"{prefix}:{index:05d}", "id": row["id"],
                     "stage": stage, "source_revision": row["source_revision"], "source_sha256": row["source_sha256"],
                     "context_pack_id": row["context_pack_id"], "context_pack_digest": row["context_pack_digest"],
                     "reviewed_translation_revision": row["translation_revision"],
                     "reviewed_translation_sha256": sha_text(row["ko"]), "reviewer_role": role,
                     "reviewer_model": None, "reviewer_effort": None, "verdict": "pending",
                     "reviewed_areas": [], "issue_ids": [], "suggested_ko": None, "notes": []})
    enforce_schema(root, "review", rows)
    return rows


def integrate(root: Path, source: list[dict[str, Any]], candidates: list[dict[str, Any]],
              reviews: list[dict[str, Any]], stage: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    report = qa(root, source, candidates, reviews, complete=True, require_stage="l2" if stage == "release" else "l1")
    if not report["ok"]:
        raise ValueError("Integration blocked: " + json.dumps(report["errors"], ensure_ascii=False))
    index = unique_index(candidates)
    out = [{**index[s["id"]], "status": "release_approved" if stage == "release" else "l1_approved"} for s in source]
    return out, report


def evaluate_poc(root: Path, matrix: dict[str, Any]) -> dict[str, Any]:
    enforce_schema(root, "verification-matrix", [matrix])
    reasons = []
    deferred = []
    if not matrix["game_version"] or not matrix["build_id"]:
        reasons.append("Game version and build ID are required")
    checks = matrix["checks"]
    unique_index(checks)
    if not any(c["category"] == "text_path" and c["status"] == "verified" for c in checks):
        reasons.append("At least one actual text path must be verified")
    if not any(c["category"] == "rebuild" and c["status"] == "verified" for c in checks):
        reasons.append("A clean-source rebuild must be verified")
    for c in checks:
        status = c["status"]
        if status == "not_applicable":
            if not c["result"]:
                reasons.append(f"{c['id']}: no rationale for not_applicable")
            continue
        if status in {"verified", "failed"}:
            if not c["result"] or not c["evidence"]:
                reasons.append(f"{c['id']}: result/evidence is missing")
            for evidence in c["evidence"]:
                if not safe(root, evidence).is_file():
                    reasons.append(f"{c['id']}: evidence file missing: {evidence}")
        if c["required_for_poc"] and status != "verified":
            reasons.append(f"{c['id']}: required PoC path is not verified")
        if status == "failed" and c["severity"] == "blocker":
            reasons.append(f"{c['id']}: blocking failure")
        if status in {"unverified", "failed"}:
            if c["deferred"] is None:
                reasons.append(f"{c['id']}: missing deferred-verification plan")
            if status == "failed" and not c["risk_accepted_by"]:
                reasons.append(f"{c['id']}: known failure lacks explicit risk acceptance")
            deferred.append(c["id"])
    return {"ok": not reasons, "gate": "blocked" if reasons else ("passed_with_unverified" if deferred else "passed"),
            "reasons": reasons, "deferred_ids": deferred,
            "limits": "Evaluates recorded conditions and evidence-file existence, not actual gameplay or evidence truth."}


def cli() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT, help="Project root (default: this kit)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    val = sub.add_parser("validate", help="Validate JSON/JSONL records against a JSON Schema")
    val.add_argument("--schema", required=True); val.add_argument("--input", required=True); val.add_argument("--allow-empty", action="store_true")
    h = sub.add_parser("hash-text"); h.add_argument("--text", required=True)
    h = sub.add_parser("hash-file"); h.add_argument("--path", required=True)
    p = sub.add_parser("make-pack"); p.add_argument("--corpus", required=True); p.add_argument("--selection", required=True); p.add_argument("--knowledge", nargs="*", default=[]); p.add_argument("--out", required=True)
    p = sub.add_parser("make-task"); p.add_argument("--role", required=True); p.add_argument("--pack", required=True); p.add_argument("--task-id", required=True); p.add_argument("--attempt", required=True); p.add_argument("--out", required=True); p.add_argument("--draft"); p.add_argument("--qa-report"); p.add_argument("--extra-input", nargs="*", default=[])
    for cmd in ["check-task", "claim-task", "release-task"]:
        p = sub.add_parser(cmd); p.add_argument("--manifest", required=True)
        if cmd != "check-task": p.add_argument("--owner", required=True)
    p = sub.add_parser("qa"); p.add_argument("--source", required=True); p.add_argument("--translations", required=True); p.add_argument("--reviews", nargs="*", default=[]); p.add_argument("--complete", action="store_true"); p.add_argument("--require-stage", choices=["l1", "l2"]); p.add_argument("--report")
    p = sub.add_parser("prepare-review"); p.add_argument("--source", required=True); p.add_argument("--translations", required=True); p.add_argument("--stage", choices=["l1", "l2"], required=True); p.add_argument("--role", required=True); p.add_argument("--prefix", required=True); p.add_argument("--out", required=True)
    p = sub.add_parser("integrate"); p.add_argument("--source", required=True); p.add_argument("--translations", nargs="+", required=True); p.add_argument("--reviews", nargs="+", required=True); p.add_argument("--stage", choices=["l1", "release"], required=True); p.add_argument("--out", required=True)
    p = sub.add_parser("evaluate-poc"); p.add_argument("--input", required=True)
    a = parser.parse_args()
    root = a.root.resolve()
    path = lambda p: safe(root, p)
    rows = lambda p: read_rows(path(p))
    result: Any = {"ok": True}
    if a.cmd == "validate":
        schema = read_json(path(a.schema)); Draft202012Validator.check_schema(schema)
        data = rows(a.input)
        problems = [f"record {i}: {e.message}" for i, item in enumerate(data, 1) for e in Draft202012Validator(schema).iter_errors(item)]
        if not data and not a.allow_empty: problems.append("Empty input; use --allow-empty only for intentionally empty issue/proposal files")
        result = {"ok": not problems, "records": len(data), "errors": problems}
    elif a.cmd == "hash-text":
        print(sha_text(a.text)); return 0
    elif a.cmd == "hash-file":
        print(sha_file(path(a.path))); return 0
    elif a.cmd == "make-pack":
        result = make_pack(root, path(a.corpus), path(a.selection), [path(p) for p in a.knowledge], path(a.out))
    elif a.cmd == "make-task":
        result = make_task(root, a.role, path(a.pack), a.task_id, a.attempt, path(a.out), path(a.draft) if a.draft else None, path(a.qa_report) if a.qa_report else None, [path(p) for p in a.extra_input])
    elif a.cmd == "check-task":
        task = check_task(root, path(a.manifest)); result = {"ok": True, "task_id": task["task_id"], "role": task["role"]}
    elif a.cmd == "claim-task":
        result = {"ok": True, "locks": claim_task(root, path(a.manifest), a.owner)}
    elif a.cmd == "release-task":
        release_task(root, path(a.manifest), a.owner)
    elif a.cmd == "qa":
        result = qa(root, rows(a.source), rows(a.translations), [r for p in a.reviews for r in rows(p)], a.complete, a.require_stage)
        if a.report:
            report_path = path(a.report)
            if report_path in {path(p) for p in [a.source, a.translations] + a.reviews}:
                raise ValueError("Report output cannot overwrite an input")
            if not a.report.startswith("work/"): raise ValueError("QA reports must be under work/")
            write_json(report_path, result)
    elif a.cmd == "prepare-review":
        target = path(a.out)
        if target.exists(): raise ValueError("Review output exists; choose a new attempt")
        if not a.out.startswith("work/"): raise ValueError("Prepared reviews must be under work/")
        records = prepare_review(root, rows(a.source), rows(a.translations), a.stage, a.role, a.prefix)
        write_rows(target, records); result = {"ok": True, "records": len(records), "output": a.out, "verdict": "pending"}
    elif a.cmd == "integrate":
        target = path(a.out)
        if not any(a.out.startswith(prefix) for prefix in ["work/", "localization/translations/"]):
            raise ValueError("Integration output must be work/ or localization/translations/")
        if target in {path(p) for p in [a.source] + a.translations + a.reviews}:
            raise ValueError("Integration must not overwrite its inputs")
        if target.exists() and target.stat().st_size:
            raise ValueError("Snapshot exists; select a new output revision")
        records, report = integrate(root, rows(a.source), [r for p in a.translations for r in rows(p)], [r for p in a.reviews for r in rows(p)], a.stage)
        write_rows(target, records); result = {"ok": True, "records": len(records), "output": a.out, "warnings": report["warnings"]}
    elif a.cmd == "evaluate-poc":
        result = evaluate_poc(root, read_json(path(a.input)))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok", True) else 1


if __name__ == "__main__":
    try:
        raise SystemExit(cli())
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
