from __future__ import annotations

import importlib.util
import json
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
    (root / "README.md").write_text(
        "\n".join(
            (
                "<!-- noctalia-versions:start -->",
                "| `gui-apps/noctalia-5.1.0` | Current |",
                "| `gui-apps/noctalia-5.0.1` | Fallback |",
                "<!-- noctalia-versions:end -->",
            )
        ),
        encoding="utf-8",
    )


class PrepareNoctaliaReleaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        create_overlay(self.root)

    def tearDown(self) -> None:
        self.directory.cleanup()

    def stable_filenames(self) -> list[str]:
        return sorted(path.name for path in (self.root / "gui-apps" / "noctalia").glob("*.ebuild"))

    def test_rotation_removes_oldest_and_retains_previous_current(self) -> None:
        result = PREPARE.prepare_release(self.root, (5, 2, 0), dry_run=False)

        self.assertEqual(result.outcome, "prepared")
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

    def test_dry_run_does_not_change_files(self) -> None:
        before = {
            path.relative_to(self.root): path.read_bytes()
            for path in self.root.rglob("*")
            if path.is_file()
        }

        result = PREPARE.prepare_release(self.root, (5, 2, 0), dry_run=True)

        after = {
            path.relative_to(self.root): path.read_bytes()
            for path in self.root.rglob("*")
            if path.is_file()
        }
        self.assertEqual(result.outcome, "dry-run")
        self.assertEqual(after, before)

    def test_second_run_is_up_to_date_after_rotation(self) -> None:
        PREPARE.prepare_release(self.root, (5, 2, 0), dry_run=False)

        result = PREPARE.prepare_release(self.root, (5, 2, 0), dry_run=False)

        self.assertEqual(result.outcome, "up-to-date")

    def test_draft_pull_request_body_marks_packaging_review(self) -> None:
        rotation = PREPARE.plan_rotation(self.root, (5, 2, 0))
        assert rotation is not None

        body = PREPARE.draft_pull_request_body(
            "v5.2.0",
            "https://github.com/noctalia-dev/noctalia/releases/tag/v5.2.0",
            rotation,
            {
                "packaging_md": "modified",
                "meson_build": "unchanged",
                "meson_options": "not present",
            },
        )

        self.assertIn("**PACKAGING.md:** modified", body)
        self.assertIn("- [ ] Test network integration, audio, and the lock screen.", body)

    def test_cli_writes_draft_pull_request_request(self) -> None:
        watcher_result = self.root / "watcher-result.json"
        request_path = self.root / "pull-request.json"
        watcher_result.write_text(
            json.dumps(
                {
                    "release": {
                        "tag": "v5.2.0",
                        "url": "https://github.com/noctalia-dev/noctalia/releases/tag/v5.2.0",
                    },
                    "packaging_changes": {
                        "packaging_md": "modified",
                        "meson_build": "unchanged",
                        "meson_options": "not present",
                    },
                }
            ),
            encoding="utf-8",
        )

        with patch.object(
            sys,
            "argv",
            [
                "prepare_noctalia_release.py",
                "--repository-root",
                str(self.root),
                "--release-tag",
                "v5.2.0",
                "--watch-result",
                str(watcher_result),
                "--pull-request-request",
                str(request_path),
            ],
        ):
            self.assertEqual(PREPARE.main(), 0)

        request = json.loads(request_path.read_text(encoding="utf-8"))
        self.assertTrue(request["draft"])
        self.assertEqual(request["head"], "automation/noctalia-v5.2.0")
