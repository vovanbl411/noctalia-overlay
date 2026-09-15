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


def write_readme(repository_root: Path, current: str, fallback: str) -> None:
    (repository_root / "README.md").write_text(
        "\n".join(
            (
                "<!-- noctalia-versions:start -->",
                "| Package | Purpose |",
                "| --- | --- |",
                f"| `gui-apps/noctalia-{current}` | Current |",
                f"| `gui-apps/noctalia-{fallback}` | Fallback |",
                "<!-- noctalia-versions:end -->",
            )
        ),
        encoding="utf-8",
    )


class FakeClient:
    def __init__(self, release, *, existing_pull_request: bool = False) -> None:
        self.release = release
        self.existing_pull_request = existing_pull_request
        self.queried_titles: list[str] = []
        self.queried_branches: list[str] = []
        self.file_shas = {
            ("PACKAGING.md", "v5.1.0"): "packaging-old",
            ("PACKAGING.md", "v5.1.1"): "packaging-new",
            ("meson.build", "v5.1.0"): "meson",
            ("meson.build", "v5.1.1"): "meson",
            ("meson_options.txt", "v5.1.0"): None,
            ("meson_options.txt", "v5.1.1"): None,
        }

    def latest_release(self):
        return self.release

    def pull_request_exists(self, title: str, branch: str) -> bool:
        self.queried_titles.append(title)
        self.queried_branches.append(branch)
        return self.existing_pull_request

    def upstream_file_sha(self, path: str, tag: str) -> str | None:
        return self.file_shas[(path, tag)]


class WatchNoctaliaReleaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.repository_root = Path(self.directory.name)
        package_dir = self.repository_root / "gui-apps" / "noctalia"
        package_dir.mkdir(parents=True)
        for version in ("5.0.1", "5.1.0", "9999", "5.2.0_beta1"):
            (package_dir / f"noctalia-{version}.ebuild").touch()
        write_readme(self.repository_root, "5.1.0", "5.0.1")

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

    def test_current_release_is_up_to_date(self) -> None:
        result = WATCHER.watch(
            FakeClient(self.release("5.1.0")), self.repository_root, dry_run=False
        )

        self.assertEqual(result.outcome, "up-to-date")

    def test_new_stable_release_is_reported_with_packaging_changes(self) -> None:
        result = WATCHER.watch(
            FakeClient(self.release("5.1.1")), self.repository_root, dry_run=False
        )

        self.assertEqual(result.outcome, "release-available")
        assert result.packaging_changes is not None
        self.assertEqual(result.packaging_changes.packaging_md, "modified")
        self.assertEqual(result.packaging_changes.meson_build, "unchanged")
        self.assertEqual(result.packaging_changes.meson_options, "not present")

    def test_dry_run_reports_but_does_not_mutate_repository(self) -> None:
        before = sorted(path.name for path in self.repository_root.rglob("*.ebuild"))

        result = WATCHER.watch(
            FakeClient(self.release("5.1.1")), self.repository_root, dry_run=True
        )

        self.assertEqual(result.outcome, "dry-run")
        self.assertEqual(sorted(path.name for path in self.repository_root.rglob("*.ebuild")), before)

    def test_existing_open_pull_request_prevents_duplicate(self) -> None:
        client = FakeClient(self.release("5.1.1"), existing_pull_request=True)

        result = WATCHER.watch(client, self.repository_root, dry_run=False)

        self.assertEqual(result.outcome, "already-reported")
        self.assertEqual(client.queried_titles, ["[release-bump] Noctalia v5.1.1"])
        self.assertEqual(client.queried_branches, ["automation/noctalia-v5.1.1"])
