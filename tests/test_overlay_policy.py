from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIRECTORY = str(Path(__file__).parents[1] / "scripts")
if SCRIPTS_DIRECTORY not in sys.path:
    sys.path.insert(0, SCRIPTS_DIRECTORY)

from overlay_policy import OverlayPolicyError, overlay_state, stable_ebuilds, validate_overlay


def create_overlay(root: Path, versions: tuple[str, ...], *, readme_versions: tuple[str, str]) -> None:
    package_dir = root / "gui-apps" / "noctalia"
    package_dir.mkdir(parents=True)
    for version in versions:
        (package_dir / f"noctalia-{version}.ebuild").touch()
    current, fallback = readme_versions
    readme = "\n".join(
        (
            "<!-- noctalia-versions:start -->",
            f"| `gui-apps/noctalia-{current}` | Current |",
            f"| `gui-apps/noctalia-{fallback}` | Fallback |",
            "<!-- noctalia-versions:end -->",
        )
    )
    for readme_name in ("README.md", "README.en.md"):
        (root / readme_name).write_text(readme, encoding="utf-8")


class OverlayPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_policy_accepts_exactly_two_stable_ebuilds(self) -> None:
        create_overlay(self.root, ("5.0.1", "5.1.0"), readme_versions=("5.1.0", "5.0.1"))

        state = validate_overlay(self.root)

        self.assertEqual(state.current.version, (5, 1, 0))
        self.assertEqual(state.fallback.version, (5, 0, 1))

    def test_policy_rejects_one_stable_ebuild(self) -> None:
        create_overlay(self.root, ("5.1.0",), readme_versions=("5.1.0", "5.0.1"))

        with self.assertRaises(OverlayPolicyError):
            validate_overlay(self.root)

    def test_policy_rejects_three_stable_ebuilds(self) -> None:
        create_overlay(
            self.root,
            ("5.0.1", "5.1.0", "5.2.0"),
            readme_versions=("5.2.0", "5.1.0"),
        )

        with self.assertRaises(OverlayPolicyError):
            validate_overlay(self.root)

    def test_stable_ebuilds_ignore_live_and_prerelease_names(self) -> None:
        create_overlay(
            self.root,
            ("5.0.1", "5.1.0", "9999", "5.2.0_alpha1", "5.2.0_beta1", "5.2.0_rc1"),
            readme_versions=("5.1.0", "5.0.1"),
        )

        versions = [ebuild.version for ebuild in stable_ebuilds(self.root)]

        self.assertEqual(versions, [(5, 0, 1), (5, 1, 0)])

    def test_policy_rejects_readme_that_contradicts_ebuilds(self) -> None:
        create_overlay(self.root, ("5.0.1", "5.1.0"), readme_versions=("5.2.0", "5.1.0"))

        with self.assertRaises(OverlayPolicyError):
            validate_overlay(self.root)

    def test_policy_rejects_english_readme_that_contradicts_ebuilds(self) -> None:
        create_overlay(self.root, ("5.0.1", "5.1.0"), readme_versions=("5.1.0", "5.0.1"))
        (self.root / "README.en.md").write_text(
            "<!-- noctalia-versions:start -->\n"
            "| `gui-apps/noctalia-5.2.0` | Current |\n"
            "| `gui-apps/noctalia-5.1.0` | Fallback |\n"
            "<!-- noctalia-versions:end -->\n",
            encoding="utf-8",
        )

        with self.assertRaises(OverlayPolicyError):
            validate_overlay(self.root)
