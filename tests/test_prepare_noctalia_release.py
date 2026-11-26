from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "prepare_noctalia_release.py"
SPEC = importlib.util.spec_from_file_location("prepare_noctalia_release", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
PREPARE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = PREPARE
SPEC.loader.exec_module(PREPARE)


def create_overlay(root: Path) -> None:
    package_dir = root / "gui-apps" / "noctalia"
    package_dir.mkdir(parents=True)
    for version in ("5.0.1", "5.1.0"):
        (package_dir / f"noctalia-{version}.ebuild").write_text(
            f"# {version}\n", encoding="utf-8"
        )
    readme = "\n".join(
        (
            "<!-- noctalia-versions:start -->",
            "| `gui-apps/noctalia-5.1.0` | Current |",
            "| `gui-apps/noctalia-5.0.1` | Fallback |",
            "<!-- noctalia-versions:end -->",
        )
    )
    for readme_name in ("README.md", "README.en.md"):
        (root / readme_name).write_text(readme, encoding="utf-8")


class PrepareNoctaliaReleaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        create_overlay(self.root)

    def tearDown(self) -> None:
        self.directory.cleanup()

    def stable_filenames(self) -> list[str]:
        return sorted(path.name for path in (self.root / "gui-apps" / "noctalia").glob("*.ebuild"))

    def run_rotation_cli(self, *, dry_run: bool) -> tuple[dict[str, str], str]:
        output = self.root / "github-output"
        summary = self.root / "summary"
        arguments = [
            str(SCRIPT_PATH),
            "--repository-root", str(self.root),
            "--release-tag", "v5.2.0",
            "--github-output", str(output),
            "--summary", str(summary),
        ]
        if dry_run:
            arguments.append("--dry-run")
        with patch.object(sys, "argv", arguments):
            self.assertEqual(PREPARE.main(), 0)
        return dict(line.split("=", 1) for line in output.read_text().splitlines()), summary.read_text()

    def test_rotation_removes_oldest_and_retains_previous_current(self) -> None:
        outputs, summary = self.run_rotation_cli(dry_run=False)

        self.assertEqual(outputs, {
            "outcome": "prepared",
            "removed_version": "5.0.1",
            "fallback_version": "5.1.0",
            "candidate_version": "5.2.0",
        })
        self.assertIn("`prepared` — Completed rotation", summary)
        self.assertEqual(
            self.stable_filenames(), ["noctalia-5.1.0.ebuild", "noctalia-5.2.0.ebuild"]
        )
        self.assertEqual(
            (self.root / "gui-apps" / "noctalia" / "noctalia-5.2.0.ebuild").read_text(
                encoding="utf-8"
            ),
            "# 5.1.0\n",
        )
        self.assertIn("noctalia-5.2.0", (self.root / "README.md").read_text(encoding="utf-8"))
        self.assertIn("noctalia-5.1.0", (self.root / "README.md").read_text(encoding="utf-8"))
        english_readme = (self.root / "README.en.md").read_text(encoding="utf-8")
        self.assertIn("| Package | Purpose |", english_readme)
        self.assertIn("noctalia-5.2.0", english_readme)

    def test_dry_run_does_not_change_files(self) -> None:
        before = {
            path.relative_to(self.root): path.read_bytes()
            for path in self.root.rglob("*")
            if path.is_file()
        }

        outputs, summary = self.run_rotation_cli(dry_run=True)

        after = {
            path.relative_to(self.root): path.read_bytes()
            for path in self.root.rglob("*")
            if path.is_file() and path.name not in {"github-output", "summary"}
        }
        self.assertEqual(outputs, {
            "outcome": "dry-run",
            "removed_version": "5.0.1",
            "fallback_version": "5.1.0",
            "candidate_version": "5.2.0",
        })
        self.assertIn("`dry-run` — Planned rotation (package files unchanged)", summary)
        self.assertEqual(after, before)

    def test_second_run_is_up_to_date_after_rotation(self) -> None:
        PREPARE.prepare_release(self.root, (5, 2, 0), dry_run=False)

        outputs, summary = self.run_rotation_cli(dry_run=False)

        self.assertEqual(outputs, {"outcome": "up-to-date"})
        self.assertIn("`up-to-date`", summary)
        self.assertEqual(
            self.stable_filenames(), ["noctalia-5.1.0.ebuild", "noctalia-5.2.0.ebuild"]
        )
