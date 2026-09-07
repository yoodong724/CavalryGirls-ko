from __future__ import annotations

import importlib.util
from pathlib import Path
import shutil
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build = load("maintenance_build_test", "tools/maintenance/build.py")
package = load("maintenance_package_test", "tools/maintenance/package.py")


class MaintenanceBuildTests(unittest.TestCase):
    def test_merged_snapshot_split_and_rejections(self) -> None:
        good = [
            {"id": "base-a", "status": "release_approved"},
            {"id": "base-b", "status": "release_approved"},
            {"id": "ui", "status": "release_approved"},
        ]
        base, ui = build.split_snapshot(good, {"base-a", "base-b"}, {"ui"})
        self.assertEqual(set(base), {"base-a", "base-b"})
        self.assertEqual(set(ui), {"ui"})
        with self.assertRaises(build.BuildError):
            build.split_snapshot(good[:-1], {"base-a", "base-b"}, {"ui"})
        with self.assertRaises(build.BuildError):
            build.split_snapshot(good + [good[0]], {"base-a", "base-b"}, {"ui"})
        rejected = [dict(row) for row in good]
        rejected[1]["status"] = "draft"
        with self.assertRaises(build.BuildError):
            build.split_snapshot(rejected, {"base-a", "base-b"}, {"ui"})

    def test_package_required_files_and_fresh_work_paths(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cg-maintenance-unit-") as temp_name:
            root = Path(temp_name)
            installer = root / "installer"
            for relative in package.INSTALLER_FILES:
                path = installer / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(relative.encode("ascii"))
            self.assertEqual(package.validate_installer_tree(installer, expected=None), sorted(package.INSTALLER_FILES))
            (installer / "restore.cmd").unlink()
            with self.assertRaises(package.PackageError):
                package.validate_installer_tree(installer, expected=None)

            work = root / "work"
            work.mkdir()
            accepted = package.resolve_fresh_work_output(work / "candidate", work)
            self.assertEqual(accepted, (work / "candidate").resolve())
            with self.assertRaises(package.PackageError):
                package.resolve_fresh_work_output(root / "outside", work)
            existing = work / "existing"
            existing.mkdir()
            with self.assertRaises(package.PackageError):
                package.resolve_fresh_work_output(existing, work)

    def test_no_legacy_work_code_dependencies(self) -> None:
        for relative in ("tools/maintenance/build.py", "tools/maintenance/package.py", "tools/maintenance/verify_windows.ps1"):
            text = (ROOT / relative).read_text(encoding="utf-8-sig")
            self.assertNotIn("work/stage", text)
            self.assertNotIn("work/hotfix", text)
            self.assertNotIn("work/poc", text)


if __name__ == "__main__":
    unittest.main()
