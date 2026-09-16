from __future__ import annotations

import importlib.util
import json
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


CURRENT_TAG = "v5.1.0"
CANDIDATE_TAG = "v5.1.1"
CURRENT_TAG_OBJECT = "a" * 40
CANDIDATE_TAG_OBJECT = "b" * 40
CURRENT_COMMIT = "c" * 40
CANDIDATE_COMMIT = "d" * 40
PGP_SIGNATURE = "-----BEGIN PGP SIGNATURE-----\nsynthetic\n-----END PGP SIGNATURE-----\n"


def write_readme(repository_root: Path, current: str, fallback: str) -> None:
    readme = "\n".join(
        (
            "<!-- noctalia-versions:start -->",
            "| Package | Purpose |",
            "| --- | --- |",
            f"| `gui-apps/noctalia-{current}` | Current |",
            f"| `gui-apps/noctalia-{fallback}` | Fallback |",
            "<!-- noctalia-versions:end -->",
        )
    )
    for readme_name in ("README.md", "README.en.md"):
        (repository_root / readme_name).write_text(readme, encoding="utf-8")


def tag_ref(tag: str, tag_object_sha: str, *, object_type: str = "tag") -> dict:
    return {
        "ref": f"refs/tags/{tag}",
        "object": {"type": object_type, "sha": tag_object_sha},
    }


def tag_object(
    tag: str,
    tag_object_sha: str,
    target_commit_sha: str,
    *,
    target_type: str = "commit",
    verified: bool = True,
    reason: str = "valid",
    signature: object = PGP_SIGNATURE,
    payload: object | None = None,
) -> dict:
    if payload is None:
        payload = "\n".join(
            (
                f"object {target_commit_sha}",
                "type commit",
                f"tag {tag}",
                "tagger Test <test@example.invalid> 0 +0000",
                "",
                "Synthetic release tag",
            )
        )
    return {
        "sha": tag_object_sha,
        "tag": tag,
        "object": {"type": target_type, "sha": target_commit_sha},
        "verification": {
            "verified": verified,
            "reason": reason,
            "signature": signature,
            "payload": payload,
            "verified_at": "2026-09-16T12:34:56Z",
        },
    }


class FakeClient:
    def __init__(self, release, *, existing_pull_request: bool = False) -> None:
        self.release = release
        self.existing_pull_request = existing_pull_request
        self.queried_titles: list[str] = []
        self.queried_branches: list[str] = []
        self.queried_refs: list[str] = []
        self.tag_refs = {
            CURRENT_TAG: tag_ref(CURRENT_TAG, CURRENT_TAG_OBJECT),
            CANDIDATE_TAG: tag_ref(CANDIDATE_TAG, CANDIDATE_TAG_OBJECT),
        }
        self.tag_objects = {
            CURRENT_TAG_OBJECT: tag_object(
                CURRENT_TAG, CURRENT_TAG_OBJECT, CURRENT_COMMIT
            ),
            CANDIDATE_TAG_OBJECT: tag_object(
                CANDIDATE_TAG, CANDIDATE_TAG_OBJECT, CANDIDATE_COMMIT
            ),
        }
        self.file_shas = {
            ("PACKAGING.md", CURRENT_COMMIT): "packaging-old",
            ("PACKAGING.md", CANDIDATE_COMMIT): "packaging-new",
            ("meson.build", CURRENT_COMMIT): "meson",
            ("meson.build", CANDIDATE_COMMIT): "meson",
            ("meson_options.txt", CURRENT_COMMIT): None,
            ("meson_options.txt", CANDIDATE_COMMIT): None,
        }

    def latest_release(self):
        return self.release

    def pull_request_exists(self, title: str, branch: str) -> bool:
        self.queried_titles.append(title)
        self.queried_branches.append(branch)
        return self.existing_pull_request

    def tag_ref(self, tag: str) -> dict:
        return self.tag_refs[tag]

    def annotated_tag(self, tag_object_sha: str) -> dict:
        return self.tag_objects[tag_object_sha]

    def upstream_file_sha(self, path: str, ref: str) -> str | None:
        self.queried_refs.append(ref)
        return self.file_shas[(path, ref)]


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

    def candidate_client(self) -> FakeClient:
        return FakeClient(self.release("5.1.1"))

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

    def test_valid_annotated_signed_tag_is_accepted(self) -> None:
        provenance = WATCHER.tag_provenance(self.candidate_client(), CANDIDATE_TAG)

        self.assertEqual(provenance.tag_object_sha, CANDIDATE_TAG_OBJECT)
        self.assertEqual(provenance.target_commit_sha, CANDIDATE_COMMIT)
        self.assertTrue(provenance.signature_verified)
        self.assertEqual(provenance.verification_reason, "valid")

    def test_lightweight_tag_is_rejected(self) -> None:
        client = self.candidate_client()
        client.tag_refs[CANDIDATE_TAG] = tag_ref(
            CANDIDATE_TAG, CANDIDATE_COMMIT, object_type="commit"
        )

        with self.assertRaises(WATCHER.WatcherError):
            WATCHER.tag_provenance(client, CANDIDATE_TAG)

    def test_unverified_tag_is_rejected(self) -> None:
        client = self.candidate_client()
        client.tag_objects[CANDIDATE_TAG_OBJECT]["verification"]["verified"] = False

        with self.assertRaises(WATCHER.WatcherError):
            WATCHER.tag_provenance(client, CANDIDATE_TAG)

    def test_non_valid_verification_reason_is_rejected(self) -> None:
        client = self.candidate_client()
        client.tag_objects[CANDIDATE_TAG_OBJECT]["verification"]["reason"] = "unsigned"

        with self.assertRaises(WATCHER.WatcherError):
            WATCHER.tag_provenance(client, CANDIDATE_TAG)

    def test_verified_at_allows_null_but_rejects_malformed_timestamp(self) -> None:
        client = self.candidate_client()
        client.tag_objects[CANDIDATE_TAG_OBJECT]["verification"]["verified_at"] = None

        provenance = WATCHER.tag_provenance(client, CANDIDATE_TAG)

        self.assertIsNone(provenance.verified_at)

        client = self.candidate_client()
        client.tag_objects[CANDIDATE_TAG_OBJECT]["verification"]["verified_at"] = "today"
        with self.assertRaises(WATCHER.WatcherError):
            WATCHER.tag_provenance(client, CANDIDATE_TAG)

    def test_missing_verification_is_rejected(self) -> None:
        client = self.candidate_client()
        del client.tag_objects[CANDIDATE_TAG_OBJECT]["verification"]

        with self.assertRaises(WATCHER.WatcherError):
            WATCHER.tag_provenance(client, CANDIDATE_TAG)

    def test_malformed_tag_object_sha_is_rejected(self) -> None:
        client = self.candidate_client()
        client.tag_refs[CANDIDATE_TAG]["object"]["sha"] = "short"

        with self.assertRaises(WATCHER.WatcherError):
            WATCHER.tag_provenance(client, CANDIDATE_TAG)

    def test_tag_object_sha_mismatch_is_rejected(self) -> None:
        client = self.candidate_client()
        client.tag_objects[CANDIDATE_TAG_OBJECT]["sha"] = "e" * 40

        with self.assertRaises(WATCHER.WatcherError):
            WATCHER.tag_provenance(client, CANDIDATE_TAG)

    def test_malformed_annotated_tag_or_target_sha_is_rejected(self) -> None:
        client = self.candidate_client()
        client.tag_objects[CANDIDATE_TAG_OBJECT]["sha"] = "short"

        with self.assertRaises(WATCHER.WatcherError):
            WATCHER.tag_provenance(client, CANDIDATE_TAG)

        client = self.candidate_client()
        client.tag_objects[CANDIDATE_TAG_OBJECT]["object"]["sha"] = "short"
        with self.assertRaises(WATCHER.WatcherError):
            WATCHER.tag_provenance(client, CANDIDATE_TAG)

    def test_non_commit_target_is_rejected(self) -> None:
        client = self.candidate_client()
        client.tag_objects[CANDIDATE_TAG_OBJECT]["object"]["type"] = "tree"

        with self.assertRaises(WATCHER.WatcherError):
            WATCHER.tag_provenance(client, CANDIDATE_TAG)

    def test_tag_name_mismatch_is_rejected(self) -> None:
        client = self.candidate_client()
        client.tag_objects[CANDIDATE_TAG_OBJECT]["tag"] = CURRENT_TAG

        with self.assertRaises(WATCHER.WatcherError):
            WATCHER.tag_provenance(client, CANDIDATE_TAG)

    def test_missing_or_non_pgp_signature_is_rejected(self) -> None:
        client = self.candidate_client()
        client.tag_objects[CANDIDATE_TAG_OBJECT]["verification"]["signature"] = None

        with self.assertRaises(WATCHER.WatcherError):
            WATCHER.tag_provenance(client, CANDIDATE_TAG)

        client = self.candidate_client()
        client.tag_objects[CANDIDATE_TAG_OBJECT]["verification"]["signature"] = "signed"
        with self.assertRaises(WATCHER.WatcherError):
            WATCHER.tag_provenance(client, CANDIDATE_TAG)

    def test_payload_mismatches_are_rejected(self) -> None:
        client = self.candidate_client()
        client.tag_objects[CANDIDATE_TAG_OBJECT]["verification"]["payload"] = "\n".join(
            (
                f"object {'e' * 40}",
                "type commit",
                f"tag {CANDIDATE_TAG}",
                "",
            )
        )
        with self.assertRaises(WATCHER.WatcherError):
            WATCHER.tag_provenance(client, CANDIDATE_TAG)

        client = self.candidate_client()
        client.tag_objects[CANDIDATE_TAG_OBJECT]["verification"]["payload"] = "\n".join(
            (
                f"object {CANDIDATE_COMMIT}",
                "type commit",
                f"tag {CURRENT_TAG}",
                "",
            )
        )
        with self.assertRaises(WATCHER.WatcherError):
            WATCHER.tag_provenance(client, CANDIDATE_TAG)

    def test_current_release_is_up_to_date_after_provenance_verification(self) -> None:
        client = FakeClient(self.release("5.1.0"))

        result = WATCHER.watch(client, self.repository_root, dry_run=False)

        self.assertEqual(result.outcome, "up-to-date")
        self.assertEqual(result.provenance.target_commit_sha, CURRENT_COMMIT)

    def test_new_stable_release_uses_verified_immutable_commits(self) -> None:
        client = self.candidate_client()

        result = WATCHER.watch(client, self.repository_root, dry_run=False)

        self.assertEqual(result.outcome, "release-available")
        self.assertEqual(set(client.queried_refs), {CURRENT_COMMIT, CANDIDATE_COMMIT})
        assert result.packaging_changes is not None
        self.assertEqual(result.packaging_changes.packaging_md, "modified")
        self.assertEqual(result.packaging_changes.meson_build, "unchanged")
        self.assertEqual(result.packaging_changes.meson_options, "not present")

    def test_result_payload_and_summary_include_provenance(self) -> None:
        result = WATCHER.watch(self.candidate_client(), self.repository_root, dry_run=False)
        payload = WATCHER.result_payload(result)
        summary_path = self.repository_root / "summary.md"

        WATCHER.write_summary(result, summary_path)

        self.assertEqual(payload["provenance"]["tag_object_sha"], CANDIDATE_TAG_OBJECT)
        self.assertIn("OpenPGP signature: verified by GitHub", summary_path.read_text())
        self.assertIn(CANDIDATE_COMMIT, summary_path.read_text())

    def test_dry_run_reports_but_does_not_mutate_repository(self) -> None:
        before = sorted(path.name for path in self.repository_root.rglob("*.ebuild"))

        result = WATCHER.watch(self.candidate_client(), self.repository_root, dry_run=True)

        self.assertEqual(result.outcome, "dry-run")
        self.assertEqual(sorted(path.name for path in self.repository_root.rglob("*.ebuild")), before)

    def test_existing_open_pull_request_prevents_duplicate(self) -> None:
        client = FakeClient(self.release("5.1.1"), existing_pull_request=True)

        result = WATCHER.watch(client, self.repository_root, dry_run=False)

        self.assertEqual(result.outcome, "already-reported")
        self.assertEqual(client.queried_titles, ["[release-bump] Noctalia v5.1.1"])
        self.assertEqual(client.queried_branches, ["automation/noctalia-v5.1.1"])
