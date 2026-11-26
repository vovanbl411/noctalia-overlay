from __future__ import annotations

import gzip
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch


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
                    "tag_type": "annotated",
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


def write_source_evidence(path: Path) -> None:
    path.write_text(json.dumps({
        "candidate": "5.2.0", "tag": "v5.2.0", "commit": "c" * 40,
        "url": HANDOFF.source_url("v5.2.0"),
        "archive": {"size": 42, "blake2b": "1" * 128, "sha512": "2" * 128},
    }))


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

    def test_prepare_summary_records_complete_validated_readiness(self) -> None:
        artifact = self.create_handoff()
        evidence = self.root / "source.json"
        write_source_evidence(evidence)
        download = self.root / "download"
        download.mkdir()
        archive = download / "artifact.zip"
        with zipfile.ZipFile(archive, "w") as zipped:
            for path in artifact.rglob("*"):
                if path.is_file():
                    zipped.write(path, path.relative_to(artifact))
        env = {
            "GITHUB_RUN_ID": "124",
            "GITHUB_RUN_ATTEMPT": "2",
            "GITHUB_SHA": "a" * 40,
            "UPLOADED_ARTIFACT_ID": "321",
            "UPLOADED_ARTIFACT_DIGEST": hashlib.sha256(archive.read_bytes()).hexdigest(),
        }
        ready = HANDOFF.record_readiness(artifact, evidence, env)
        summary_path = self.root / "summary"
        output = self.root / "github-output"
        subprocess.run(
            [
                sys.executable, str(SCRIPT_PATH), "ready",
                "--handoff-root", str(artifact), "--evidence", str(evidence),
            ],
            env={
                **os.environ,
                **env,
                "GITHUB_OUTPUT": str(output),
                "GITHUB_STEP_SUMMARY": str(summary_path),
                "GH_TOKEN": "test-credential-value",
                "UNRELATED_SETTING": "test-unrelated-value",
            },
            check=True,
            capture_output=True,
        )
        summary = summary_path.read_text()
        evidence_json = summary.split("```json\n", 1)[1].split("\n```", 1)[0]
        self.assertEqual(json.loads(evidence_json), ready)
        self.assertEqual(json.loads(output.read_text().removeprefix("readiness=")), ready)
        self.assertNotIn("test-credential-value", summary)
        self.assertNotIn("test-unrelated-value", summary)

        destination = self.root / "unpacked"
        admitted = HANDOFF.unpack_verified_artifact(download, destination, ready)
        self.assertEqual(admitted, HANDOFF.validate_handoff(artifact))
        self.assertEqual(
            {path.relative_to(destination).as_posix() for path in destination.rglob("*") if path.is_file()},
            {"release.json", "README.md", "README.en.md", HANDOFF.MANIFEST_PATH, HANDOFF.version_path(admitted.candidate)},
        )

    def test_only_a_real_release_is_publishable(self) -> None:
        self.assertFalse(HANDOFF.publish_is_required("up-to-date", False))
        self.assertFalse(HANDOFF.publish_is_required("already-reported", False))
        self.assertFalse(HANDOFF.publish_is_required("dry-run", True))
        self.assertFalse(HANDOFF.publish_is_required("release-available", True))
        self.assertTrue(HANDOFF.publish_is_required("release-available", False))

    def test_real_release_creates_and_applies_valid_handoff(self) -> None:
        handoff_root = self.create_handoff()
        self.assertEqual(
            self.load_metadata(handoff_root)["schema_version"],
            HANDOFF.HANDOFF_SCHEMA_VERSION,
        )

        handoff = HANDOFF.apply_handoff(handoff_root, self.fresh_checkout)

        self.assertEqual(handoff.branch, "automation/noctalia-v5.2.0")
        self.assertTrue(
            (self.fresh_checkout / "gui-apps" / "noctalia" / "noctalia-5.2.0.ebuild").exists()
        )
        self.assertFalse(
            (self.fresh_checkout / "gui-apps" / "noctalia" / "noctalia-5.0.1.ebuild").exists()
        )
        self.assertIn(
            "noctalia-5.2.0",
            (self.fresh_checkout / "README.en.md").read_text(encoding="utf-8"),
        )
        self.assertEqual(handoff.provenance.target_commit_sha, "c" * 40)

    def test_handoff_accepts_valid_lightweight_provenance(self) -> None:
        handoff_root = self.create_handoff()
        metadata = self.load_metadata(handoff_root)
        provenance = metadata["provenance"]
        assert isinstance(provenance, dict)
        provenance.update(
            tag_type="lightweight",
            tag_object_sha=None,
            signature_verified=False,
            verification_reason="not-applicable",
            verified_at=None,
        )
        self.write_metadata(handoff_root, metadata)

        handoff = HANDOFF.validate_handoff(handoff_root)

        self.assertEqual(handoff.provenance.tag_type, "lightweight")
        self.assertIsNone(handoff.provenance.tag_object_sha)
        self.assertFalse(handoff.provenance.signature_verified)

    def test_handoff_accepts_annotated_unsigned_provenance(self) -> None:
        handoff_root = self.create_handoff()
        metadata = self.load_metadata(handoff_root)
        provenance = metadata["provenance"]
        assert isinstance(provenance, dict)
        provenance.update(signature_verified=False, verification_reason="unsigned")
        self.write_metadata(handoff_root, metadata)

        handoff = HANDOFF.validate_handoff(handoff_root)

        self.assertFalse(handoff.provenance.signature_verified)
        self.assertEqual(handoff.provenance.verification_reason, "unsigned")

    def test_handoff_rejects_inconsistent_provenance_combinations(self) -> None:
        invalid_updates = (
            {"signature_verified": True, "verification_reason": "unsigned"},
            {"signature_verified": False, "verification_reason": "valid"},
            {"signature_verified": False, "verification_reason": "not-applicable"},
            {"tag_type": "lightweight", "tag_object_sha": "b" * 40},
            {"tag_type": "lightweight", "signature_verified": True},
            {"tag_type": "lightweight", "verification_reason": "unsigned"},
            {"tag_type": "mystery"},
        )
        for index, updates in enumerate(invalid_updates):
            handoff_root = self.create_handoff() if index == 0 else self.root / f"invalid-{index}"
            if index > 0:
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
            provenance.update(updates)
            self.write_metadata(handoff_root, metadata)
            with self.subTest(updates=updates), self.assertRaises(HANDOFF.HandoffError):
                HANDOFF.validate_handoff(handoff_root)

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
            '{"schema_version": 2, "schema_version": 2}\n', encoding="utf-8"
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

class IsolatedGitTests(unittest.TestCase):
    def setUp(self) -> None:
        environment = patch.dict(os.environ, {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z",
            "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z",
            "LC_ALL": "C",
        })
        environment.start()
        self.addCleanup(environment.stop)


class SourceArchiveTests(IsolatedGitTests):
    def setUp(self) -> None:
        super().setUp()
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.archive = self.root / "noctalia-5.2.0.tar.gz"
        HANDOFF.git(self.root, "init", "-q")
        HANDOFF.git(self.root, "config", "user.name", "Archive test")
        HANDOFF.git(self.root, "config", "user.email", "test@example.invalid")
        (self.root / "VERSION").write_text("5.2.0\n", encoding="utf-8")
        HANDOFF.git(self.root, "add", "VERSION")
        HANDOFF.git(self.root, "commit", "-qm", "Release")
        self.commit = HANDOFF.git(self.root, "rev-parse", "HEAD").decode().strip()
        self.tar = HANDOFF.git(self.root, "archive", "--format=tar", self.commit)

    def write_archive(self, data: bytes) -> None:
        self.archive.write_bytes(gzip.compress(data, mtime=0))

    def test_complete_git_archive_with_matching_commit(self) -> None:
        self.write_archive(self.tar)
        HANDOFF.verify_git_archive(self.archive, self.commit)

    def test_commit_mismatch_when_version_is_identical(self) -> None:
        HANDOFF.git(self.root, "commit", "--allow-empty", "-qm", "Moved tag")
        self.write_archive(HANDOFF.git(self.root, "archive", "--format=tar", "HEAD"))
        with self.assertRaisesRegex(HANDOFF.HandoffError, "different from discovery"):
            HANDOFF.verify_git_archive(self.archive, self.commit)

    def test_missing_and_malformed_tar_commit_id(self) -> None:
        tree = HANDOFF.git(self.root, "rev-parse", "HEAD^{tree}").decode().strip()
        for tar in (
            HANDOFF.git(self.root, "archive", "--format=tar", tree),
            self.tar.replace(self.commit.encode(), b"q" * 40, 1),
        ):
            with self.subTest(tar_header=tar[:20]):
                self.write_archive(tar)
                with self.assertRaisesRegex(HANDOFF.HandoffError, "commit ID"):
                    HANDOFF.verify_git_archive(self.archive, self.commit)

    def test_corrupt_gzip_and_truncated_or_corrupt_tar_after_commit_header(self) -> None:
        compressed = gzip.compress(self.tar, mtime=0)
        for data in (
            compressed[:-5],
            compressed[:-8] + bytes(8),
            gzip.compress(self.tar[:1536], mtime=0),
            gzip.compress(self.tar[:1024] + b"!" + self.tar[1025:], mtime=0),
            gzip.compress(self.tar + b"trailing junk", mtime=0),
        ):
            with self.subTest(size=len(data)):
                self.archive.write_bytes(data)
                with self.assertRaisesRegex(HANDOFF.HandoffError, "gzip/tar|Source tar"):
                    HANDOFF.verify_git_archive(self.archive, self.commit)

    def test_canonical_download_uses_discovery_and_a_fresh_distdir(self) -> None:
        # Run the production downloader with a fake curl response and real Git validation.
        write_overlay(self.root, ("5.1.0", "5.2.0"))
        (self.root / HANDOFF.version_path((5, 2, 0))).write_text(HANDOFF.SOURCE_URI + "\n")
        watcher_result = self.root / "watch.json"
        write_watcher_result(watcher_result)
        payload = json.loads(watcher_result.read_text())
        payload["provenance"]["target_commit_sha"] = self.commit
        watcher_result.write_text(json.dumps(payload))
        summary = self.root / "summary"
        distdir = self.root / "distfiles"

        def run(command, **kwargs):
            if command[0] != "curl":
                return original_run(command, **kwargs)
            self.assertEqual(command[-1], HANDOFF.source_url("v5.2.0"))
            self.assertEqual(command[command.index("--proto-redir") + 1], "=https")
            path = Path(command[command.index("--output") + 1])
            self.assertEqual(path, distdir / "noctalia-5.2.0.tar.gz")
            self.assertEqual(list(distdir.iterdir()), [])
            path.write_bytes(gzip.compress(self.tar, mtime=0))
            return subprocess.CompletedProcess(command, 0, command[-1])

        original_run = subprocess.run
        with patch.object(HANDOFF.subprocess, "run", side_effect=run):
            evidence = HANDOFF.verify_source(self.root, watcher_result, "v5.2.0", distdir, summary)
            with self.assertRaises(FileExistsError):
                HANDOFF.verify_source(self.root, watcher_result, "v5.2.0", distdir, summary)
        self.assertEqual(evidence["commit"], self.commit)
        self.assertEqual(evidence["archive"], HANDOFF.archive_integrity(distdir / self.archive.name))

    def test_source_requires_exact_declaration_before_download(self) -> None:
        write_overlay(self.root, ("5.1.0", "5.2.0"))
        ebuild = self.root / HANDOFF.version_path((5, 2, 0))
        ebuild.write_text(f"  {HANDOFF.SOURCE_URI}\n")
        watcher_result = self.root / "watch.json"
        write_watcher_result(watcher_result)
        distdir = self.root / "distfiles"

        with (
            patch.object(HANDOFF.subprocess, "run") as run,
            self.assertRaises(HANDOFF.HandoffError) as error,
        ):
            HANDOFF.verify_source(
                self.root, watcher_result, "v5.2.0", distdir, self.root / "summary"
            )

        self.assertIn(str(ebuild), str(error.exception))
        self.assertIn(HANDOFF.SOURCE_URI, str(error.exception))
        self.assertIn("canonical archive verification", str(error.exception))
        self.assertFalse(distdir.exists())
        run.assert_not_called()

    def test_compare_manifest_hashes_size_and_retained_fallback(self) -> None:
        self.write_archive(self.tar)
        integrity = HANDOFF.archive_integrity(self.archive)
        base = self.root / "base-manifest"
        manifest = self.root / "candidate-manifest"
        fallback = manifest_line("5.1.0", 19, "a" * 128, "b" * 128)
        candidate = manifest_line("5.2.0", integrity["size"], integrity["blake2b"], integrity["sha512"])
        base.write_text(fallback)
        manifest.write_text(fallback + candidate)
        HANDOFF.verify_manifest(manifest, base, (5, 2, 0), (5, 1, 0), integrity)
        for actual, error in (
            (candidate.replace(str(integrity["size"]), "95", 1), "DIST size"),
            (candidate.replace(integrity["blake2b"], "1" * 128), "BLAKE2B"),
            (candidate.replace(integrity["sha512"], "1" * 128), "SHA512"),
        ):
            with self.subTest(error=error):
                manifest.write_text(fallback + actual)
                with self.assertRaisesRegex(HANDOFF.HandoffError, error):
                    HANDOFF.verify_manifest(manifest, base, (5, 2, 0), (5, 1, 0), integrity)
        manifest.write_text(fallback.replace(" 19 ", " 20 ") + candidate)
        with self.assertRaisesRegex(HANDOFF.HandoffError, "Fallback DIST"):
            HANDOFF.verify_manifest(manifest, base, (5, 2, 0), (5, 1, 0), integrity)


def manifest_line(version, size, blake2b, sha512) -> str:
    return f"DIST noctalia-{version}.tar.gz {size} BLAKE2B {blake2b} SHA512 {sha512}\n"
