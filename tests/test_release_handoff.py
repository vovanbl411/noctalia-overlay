from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "release_handoff.py"
SPEC = importlib.util.spec_from_file_location("release_handoff", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
HANDOFF = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = HANDOFF
SPEC.loader.exec_module(HANDOFF)


def write_overlay(root: Path, versions: tuple[str, str]) -> None:
    package_directory = root / "gui-apps" / "noctalia"
    package_directory.mkdir(parents=True)
    fallback, current = versions
    for version in versions:
        (package_directory / f"noctalia-{version}.ebuild").write_text(
            f"# {version}\n", encoding="utf-8"
        )
    (package_directory / "Manifest").write_text("DIST example 1 BLAKE2B deadbeef\n", encoding="utf-8")
    (root / "README.md").write_text(
        "\n".join(
            (
                "<!-- noctalia-versions:start -->",
                f"| `gui-apps/noctalia-{current}` | Current |",
                f"| `gui-apps/noctalia-{fallback}` | Fallback |",
                "<!-- noctalia-versions:end -->",
            )
        ),
        encoding="utf-8",
    )


def write_watcher_result(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "release": {
                    "tag": "v5.2.0",
                    "url": "https://github.com/noctalia-dev/noctalia/releases/tag/v5.2.0",
                },
                "provenance": {
                    "tag": "v5.2.0",
                    "tag_object_sha": "b" * 40,
                    "target_commit_sha": "c" * 40,
                    "signature_verified": True,
                    "verification_reason": "valid",
                    "verified_at": "2026-09-16T12:34:56Z",
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


class ReleaseHandoffTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.source = self.root / "prepared"
        self.fresh_checkout = self.root / "fresh"
        write_overlay(self.source, ("5.1.0", "5.2.0"))
        write_overlay(self.fresh_checkout, ("5.0.1", "5.1.0"))
        self.watcher_result = self.root / "watcher-result.json"
        write_watcher_result(self.watcher_result)

    def tearDown(self) -> None:
        self.directory.cleanup()

    def create_handoff(self) -> Path:
        handoff_root = self.root / "release-handoff"
        HANDOFF.create_handoff(
            self.source,
            handoff_root,
            self.watcher_result,
            release_tag="v5.2.0",
            base_commit="a" * 40,
            removed=(5, 0, 1),
            fallback=(5, 1, 0),
            candidate=(5, 2, 0),
        )
        return handoff_root

    def load_metadata(self, handoff_root: Path) -> dict[str, object]:
        metadata_path = handoff_root / "release.json"
        return json.loads(metadata_path.read_text(encoding="utf-8"))

    def write_metadata(self, handoff_root: Path, metadata: dict[str, object]) -> None:
        (handoff_root / "release.json").write_text(
            json.dumps(metadata), encoding="utf-8"
        )

    def test_only_a_real_release_is_publishable(self) -> None:
        self.assertFalse(HANDOFF.publish_is_required("up-to-date", False))
        self.assertFalse(HANDOFF.publish_is_required("already-reported", False))
        self.assertFalse(HANDOFF.publish_is_required("dry-run", True))
        self.assertTrue(HANDOFF.publish_is_required("release-available", False))

    def test_real_release_creates_and_applies_valid_handoff(self) -> None:
        handoff_root = self.create_handoff()

        handoff = HANDOFF.apply_handoff(handoff_root, self.fresh_checkout)

        self.assertEqual(handoff.branch, "automation/noctalia-v5.2.0")
        self.assertTrue(
            (self.fresh_checkout / "gui-apps" / "noctalia" / "noctalia-5.2.0.ebuild").exists()
        )
        self.assertFalse(
            (self.fresh_checkout / "gui-apps" / "noctalia" / "noctalia-5.0.1.ebuild").exists()
        )
        self.assertEqual(handoff.provenance.target_commit_sha, "c" * 40)

    def test_draft_pull_request_contains_verified_provenance(self) -> None:
        handoff_root = self.create_handoff()
        handoff = HANDOFF.validate_handoff(handoff_root)

        body = HANDOFF.draft_pull_request_body(handoff)

        self.assertIn("OpenPGP signature: verified by GitHub", body)
        self.assertIn(f"Tag object: `{'b' * 40}`", body)
        self.assertIn(
            "Review upstream provenance/signing identity if anything looks unusual.",
            body,
        )

    def test_rejects_handoff_for_a_different_main_commit(self) -> None:
        handoff_root = self.create_handoff()

        with self.assertRaises(HANDOFF.HandoffError):
            HANDOFF.apply_handoff(
                handoff_root,
                self.fresh_checkout,
                expected_base_commit="b" * 40,
            )

    def test_sanitized_copy_excludes_git_metadata(self) -> None:
        (self.source / ".git").mkdir()
        (self.source / ".git" / "config").write_text("[core]\n", encoding="utf-8")
        destination = self.root / "sanitized"

        HANDOFF.copy_sanitized_workspace(self.source, destination)

        self.assertFalse((destination / ".git").exists())
        self.assertTrue((destination / "README.md").is_file())

    def test_container_integrity_allows_only_manifest_change(self) -> None:
        baseline = self.root / "before-container.json"
        HANDOFF.write_workspace_integrity(self.source, baseline)
        (self.source / "gui-apps" / "noctalia" / "Manifest").write_text(
            "DIST updated 1 BLAKE2B deadbeef\n", encoding="utf-8"
        )

        HANDOFF.verify_workspace_integrity(self.source, baseline)

    def test_container_integrity_rejects_non_manifest_change(self) -> None:
        baseline = self.root / "before-container.json"
        HANDOFF.write_workspace_integrity(self.source, baseline)
        (self.source / "README.md").write_text("changed\n", encoding="utf-8")

        with self.assertRaises(HANDOFF.HandoffError):
            HANDOFF.verify_workspace_integrity(self.source, baseline)

    def test_rejects_unexpected_file(self) -> None:
        handoff_root = self.create_handoff()
        (handoff_root / "unexpected.txt").write_text("unexpected", encoding="utf-8")

        with self.assertRaises(HANDOFF.HandoffError):
            HANDOFF.validate_handoff(handoff_root)

    def test_rejects_path_traversal_version(self) -> None:
        handoff_root = self.create_handoff()
        metadata = self.load_metadata(handoff_root)
        metadata["candidate_version"] = "../5.2.0"
        self.write_metadata(handoff_root, metadata)

        with self.assertRaises(HANDOFF.HandoffError):
            HANDOFF.validate_handoff(handoff_root)

    def test_rejects_symlink(self) -> None:
        handoff_root = self.create_handoff()
        candidate = handoff_root / "gui-apps" / "noctalia" / "noctalia-5.2.0.ebuild"
        candidate.unlink()
        os.symlink("/dev/null", candidate)

        with self.assertRaises(HANDOFF.HandoffError):
            HANDOFF.validate_handoff(handoff_root)

    def test_rejects_invalid_version(self) -> None:
        handoff_root = self.create_handoff()
        metadata = self.load_metadata(handoff_root)
        metadata["fallback_version"] = "not-a-version"
        self.write_metadata(handoff_root, metadata)

        with self.assertRaises(HANDOFF.HandoffError):
            HANDOFF.validate_handoff(handoff_root)

    def test_rejects_missing_or_malformed_provenance(self) -> None:
        handoff_root = self.create_handoff()
        metadata = self.load_metadata(handoff_root)
        del metadata["provenance"]
        self.write_metadata(handoff_root, metadata)

        with self.assertRaises(HANDOFF.HandoffError):
            HANDOFF.validate_handoff(handoff_root)

        handoff_root = self.root / "malformed-provenance"
        HANDOFF.create_handoff(
            self.source,
            handoff_root,
            self.watcher_result,
            release_tag="v5.2.0",
            base_commit="a" * 40,
            removed=(5, 0, 1),
            fallback=(5, 1, 0),
            candidate=(5, 2, 0),
        )
        metadata = self.load_metadata(handoff_root)
        provenance = metadata["provenance"]
        assert isinstance(provenance, dict)
        provenance["target_commit_sha"] = "bad"
        self.write_metadata(handoff_root, metadata)

        with self.assertRaises(HANDOFF.HandoffError):
            HANDOFF.validate_handoff(handoff_root)

    def test_rejects_invalid_schema_version_type(self) -> None:
        handoff_root = self.create_handoff()
        metadata = self.load_metadata(handoff_root)
        metadata["schema_version"] = True
        self.write_metadata(handoff_root, metadata)

        with self.assertRaises(HANDOFF.HandoffError):
            HANDOFF.validate_handoff(handoff_root)

    def test_rejects_duplicate_json_fields(self) -> None:
        handoff_root = self.create_handoff()
        (handoff_root / "release.json").write_text(
            '{"schema_version": 1, "schema_version": 1}\n', encoding="utf-8"
        )

        with self.assertRaises(HANDOFF.HandoffError):
            HANDOFF.validate_handoff(handoff_root)

    def test_rejects_invalid_candidate_filename(self) -> None:
        handoff_root = self.create_handoff()
        candidate = handoff_root / "gui-apps" / "noctalia" / "noctalia-5.2.0.ebuild"
        candidate.rename(candidate.with_name("noctalia-5.2.1.ebuild"))

        with self.assertRaises(HANDOFF.HandoffError):
            HANDOFF.validate_handoff(handoff_root)

    def test_rejects_missing_required_file(self) -> None:
        handoff_root = self.create_handoff()
        (handoff_root / "gui-apps" / "noctalia" / "Manifest").unlink()

        with self.assertRaises(HANDOFF.HandoffError):
            HANDOFF.validate_handoff(handoff_root)

    def test_rejects_post_apply_overlay_policy_failure(self) -> None:
        handoff_root = self.create_handoff()
        (handoff_root / "README.md").write_text(
            "<!-- noctalia-versions:start -->\n"
            "| `gui-apps/noctalia-5.9.0` | Current |\n"
            "| `gui-apps/noctalia-5.1.0` | Fallback |\n"
            "<!-- noctalia-versions:end -->\n",
            encoding="utf-8",
        )

        with self.assertRaises(HANDOFF.HandoffError):
            HANDOFF.apply_handoff(handoff_root, self.fresh_checkout)
