from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "watch_noctalia_release.py"
SPEC = importlib.util.spec_from_file_location("watch_noctalia_release", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
WATCHER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = WATCHER
SPEC.loader.exec_module(WATCHER)


class FakeClient:
    def __init__(self, release, *, existing_issue: bool = False) -> None:
        self.release = release
        self.existing_issue = existing_issue
        self.created_issues: list[tuple[str, str]] = []

    def latest_release(self):
        return self.release

    def issue_exists(self, title: str) -> bool:
        return self.existing_issue

    def create_issue(self, title: str, body: str) -> str:
        self.created_issues.append((title, body))
        return "https://github.com/vovanbl411/noctalia-overlay/issues/1"


class WatchNoctaliaReleaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.repository_root = Path(self.directory.name)
        package_dir = self.repository_root / "gui-apps" / "noctalia"
        package_dir.mkdir(parents=True)
        for version in ("5.0.1", "5.1.0", "9999", "5.2.0_beta1"):
            (package_dir / f"noctalia-{version}.ebuild").touch()

    def tearDown(self) -> None:
        self.directory.cleanup()

    def release(self, version: str):
        return WATCHER.parse_stable_release(
            {
                "draft": False,
                "prerelease": False,
                "tag_name": f"v{version}",
                "html_url": f"https://github.com/noctalia-dev/noctalia/releases/tag/v{version}",
            }
        )

    def test_latest_packaged_version_ignores_live_and_prerelease_ebuilds(self) -> None:
        self.assertEqual(WATCHER.latest_packaged_version(self.repository_root), (5, 1, 0))

    def test_prerelease_is_rejected(self) -> None:
        with self.assertRaises(WATCHER.WatcherError):
            WATCHER.parse_stable_release(
                {
                    "draft": False,
                    "prerelease": True,
                    "tag_name": "v5.1.1",
                    "html_url": "https://github.com/noctalia-dev/noctalia/releases/tag/v5.1.1",
                }
            )

    def test_current_release_does_not_create_an_issue(self) -> None:
        client = FakeClient(self.release("5.1.0"))

        result = WATCHER.watch(client, self.repository_root, dry_run=False)

        self.assertEqual(result.outcome, "up-to-date")
        self.assertEqual(client.created_issues, [])

    def test_new_release_dry_run_does_not_create_an_issue(self) -> None:
        client = FakeClient(self.release("5.1.1"))

        result = WATCHER.watch(client, self.repository_root, dry_run=True)

        self.assertEqual(result.outcome, "dry-run")
        self.assertEqual(client.created_issues, [])

    def test_new_release_creates_one_issue(self) -> None:
        client = FakeClient(self.release("5.1.1"))

        result = WATCHER.watch(client, self.repository_root, dry_run=False)

        self.assertEqual(result.outcome, "issue-created")
        self.assertEqual(len(client.created_issues), 1)
        self.assertIn("Noctalia v5.1.1", client.created_issues[0][0])

    def test_existing_issue_prevents_duplicate(self) -> None:
        client = FakeClient(self.release("5.1.1"), existing_issue=True)

        result = WATCHER.watch(client, self.repository_root, dry_run=False)

        self.assertEqual(result.outcome, "already-reported")
        self.assertEqual(client.created_issues, [])
