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

from prepare_noctalia_release import prepare_release


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

    def test_draft_pull_request_contains_annotated_signed_provenance(self) -> None:
        handoff_root = self.create_handoff()
        handoff = HANDOFF.validate_handoff(handoff_root)

        body = HANDOFF.draft_pull_request_body(handoff)

        self.assertIn("GitHub tag signature verification: verified", body)
        self.assertIn(f"Tag object: `{'b' * 40}`", body)
        self.assertIn(
            "Review upstream provenance/signing identity if anything looks unusual.",
            body,
        )

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
        body = HANDOFF.draft_pull_request_body(handoff)
        self.assertIn("Tag type: lightweight", body)
        self.assertIn("GitHub tag signature verification: not applicable", body)
        self.assertNotIn("OpenPGP signature: verified by GitHub", body)

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


class PublicationTests(IsolatedGitTests):
    branch = "automation/noctalia-v5.2.0"
    candidate_ref = f"refs/heads/{branch}"

    def setUp(self) -> None:
        super().setUp()
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.checkout = self.root / "checkout"
        self.remote = self.root / "remote.git"
        self.artifact = self.root / "handoff"
        self.download = self.root / "download"
        self.output = self.root / "unpacked"
        self.summary = self.root / "summary"
        write_overlay(self.checkout, ("5.0.1", "5.1.0"))
        ebuild = HANDOFF.SOURCE_URI + "\n"
        (self.checkout / HANDOFF.version_path((5, 1, 0))).write_text(ebuild)
        self.fallback = manifest_line("5.1.0", 19, "a" * 128, "b" * 128)
        (self.checkout / HANDOFF.MANIFEST_PATH).write_text(
            manifest_line("5.0.1", 12, "d" * 128, "e" * 128) + self.fallback,
        )
        HANDOFF.git(self.checkout, "init", "-q", "-b", "main")
        HANDOFF.git(self.checkout, "config", "user.name", "Local test")
        HANDOFF.git(self.checkout, "config", "user.email", "test@example.invalid")
        HANDOFF.git(self.checkout, "add", ".")
        HANDOFF.git(self.checkout, "commit", "-qm", "Base")
        self.base = self.head()
        HANDOFF.git(self.root, "init", "--bare", "-q", str(self.remote))
        HANDOFF.git(self.checkout, "remote", "add", "origin", str(self.remote))
        HANDOFF.git(self.checkout, "push", "origin", "HEAD:refs/heads/main")

        prepared = self.root / "prepared"
        HANDOFF.copy_sanitized_workspace(self.checkout, prepared)
        prepare_release(prepared, (5, 2, 0), dry_run=False)
        (prepared / HANDOFF.MANIFEST_PATH).write_text(
            self.fallback + manifest_line("5.2.0", 42, "1" * 128, "2" * 128)
        )
        watcher = self.root / "watcher.json"
        write_watcher_result(watcher)
        HANDOFF.create_handoff(
            prepared, self.artifact, watcher, release_tag="v5.2.0", base_commit=self.base,
            removed=(5, 0, 1), fallback=(5, 1, 0), candidate=(5, 2, 0),
        )
        self.evidence = self.root / "source.json"
        self.evidence.write_text(json.dumps({
            "candidate": "5.2.0", "tag": "v5.2.0", "commit": "c" * 40,
            "url": HANDOFF.source_url("v5.2.0"),
            "archive": {"size": 42, "blake2b": "1" * 128, "sha512": "2" * 128},
        }))
        self.env = {
            "GITHUB_RUN_ID": "124", "GITHUB_RUN_ATTEMPT": "2", "GITHUB_SHA": self.base,
            "GITHUB_REPOSITORY": HANDOFF.REPOSITORY, "GITHUB_REF": HANDOFF.MAIN_REF,
            "GITHUB_WORKFLOW_REF": HANDOFF.WORKFLOW_REF, "GITHUB_EVENT_NAME": "schedule",
            "DRY_RUN": "false", "PREPARE_RESULT": "success", "UPLOADED_ARTIFACT_ID": "321",
        }
        self.download.mkdir()
        self.zip = self.download / "artifact.zip"
        self.pack_artifact()
        self.client = HANDOFF.GitHubClient("fake-token", HANDOFF.REPOSITORY)
        self.pr_requests = []
        self.pull_requests = []
        self.search = {"total_count": 0, "incomplete_results": False, "items": []}
        self.post_result = None
        self.git_calls = []
        self.request_count = 0
        self.on_request = lambda: None
        self.before_push = lambda: None

    def head(self) -> str:
        return HANDOFF.git(self.checkout, "rev-parse", "HEAD").decode().strip()

    def remote_head(self) -> str | None:
        return HANDOFF.remote_ref(self.checkout, self.candidate_ref, None)

    def set_remote_ref(self, ref: str, sha: str) -> None:
        HANDOFF.git(self.remote, "update-ref", ref, sha)

    def pack_artifact(self, extra=None) -> None:
        with zipfile.ZipFile(self.zip, "w") as archive:
            for path in self.artifact.rglob("*"):
                if path.is_file():
                    archive.write(path, path.relative_to(self.artifact))
            if extra is not None:
                entry, content = extra
                archive.writestr(entry, content)
        self.env["UPLOADED_ARTIFACT_DIGEST"] = hashlib.sha256(self.zip.read_bytes()).hexdigest()
        self.ready = HANDOFF.record_readiness(self.artifact, self.evidence, self.env)
        self.env["PREPARE_READY"] = json.dumps(self.ready)

    def pr(self, *, state="open", head=None) -> dict:
        repo = {"full_name": HANDOFF.REPOSITORY}
        return {
            "number": 18, "html_url": f"https://github.com/{HANDOFF.REPOSITORY}/pull/18",
            "title": "[release-bump] Noctalia v5.2.0", "state": state, "draft": True,
            "head": {"ref": self.branch, "sha": head or self.head(), "repo": repo},
            "base": {"ref": "main", "sha": self.base, "repo": repo},
        }

    def request(self, url, *, query=None, allow_not_found=False, data=None):
        self.request_count += 1
        self.on_request()
        if data is not None:
            self.assertEqual(url, f"https://api.github.com/repos/{HANDOFF.REPOSITORY}/pulls")
            self.pr_requests.append(data)
            if isinstance(self.post_result, Exception):
                raise self.post_result
            return self.pr() if self.post_result is None else self.post_result
        if url.startswith("https://api.github.com/search/issues?"):
            return self.search
        self.assertEqual(query["head"], f"vovanbl411:{self.branch}")
        return [p for p in self.pull_requests if query["state"] in ("all", p["state"])]

    def publish(self) -> None:
        git = HANDOFF.git

        def trace(root, *args, **kwargs):
            self.git_calls.append(args)
            if args[0] == "push":
                self.before_push()
            return git(root, *args, **kwargs)

        with (
            patch.object(HANDOFF, "git", side_effect=trace),
            patch.object(self.client, "_request_payload", side_effect=self.request),
        ):
            HANDOFF.publish_candidate(
                self.checkout, self.download, self.output, self.summary, self.env,
                self.client, remote_url=str(self.remote),
            )

    def assert_no_writes(self) -> None:
        self.assertFalse(any(args[0] == "push" for args in self.git_calls))
        self.assertEqual(self.pr_requests, [])

    def test_valid_publication_creates_candidate_and_draft(self) -> None:
        self.assertEqual(HANDOFF.publication_preflight(self.checkout, self.env)["artifact"], "321")
        self.publish()
        self.assertEqual(self.remote_head(), self.head())
        self.assertEqual(len(self.pr_requests), 1)
        self.assertEqual(self.pr_requests[0]["head"], self.branch)
        self.assertEqual(self.pr_requests[0]["base"], "main")
        self.assertIs(self.pr_requests[0]["draft"], True)
        body = self.pr_requests[0]["body"]
        self.assertIn("https://github.com/vovanbl411/noctalia-overlay/actions/runs/124", body)
        self.assertIn("Prepare attempt: `2`", body)
        self.assertIn(f"Validated base SHA: `{self.base}`", body)
        self.assertIn(f"BLAKE2B: `{'1' * 128}`", body)
        self.assertIn(f"Target commit: `{'c' * 40}`", body)
        self.assertIn("- [ ] Test network integration, audio, and the lock screen.", body)
        self.assertIn("Full source build and runtime checks: not run", body)
        self.assertIn("Draft PR confirmed", self.summary.read_text())

    def test_prepare_summary_records_complete_validated_readiness(self) -> None:
        output = self.root / "github-output"
        subprocess.run(
            [
                sys.executable, str(SCRIPT_PATH), "ready",
                "--handoff-root", str(self.artifact), "--evidence", str(self.evidence),
            ],
            env={
                **os.environ,
                **self.env,
                "GITHUB_OUTPUT": str(output),
                "GITHUB_STEP_SUMMARY": str(self.summary),
                "GH_TOKEN": "test-credential-value",
                "UNRELATED_SETTING": "test-unrelated-value",
            },
            check=True,
            capture_output=True,
        )
        summary = self.summary.read_text()
        evidence_json = summary.split("```json\n", 1)[1].split("\n```", 1)[0]
        self.assertEqual(json.loads(evidence_json), self.ready)
        self.assertEqual(json.loads(output.read_text().removeprefix("readiness=")), self.ready)
        self.assertNotIn("test-credential-value", summary)
        self.assertNotIn("test-unrelated-value", summary)

    def test_context_is_case_sensitive_and_blocks_untrusted_runs(self) -> None:
        for key, value in (
            ("GITHUB_REPOSITORY", "Vovanbl411/noctalia-overlay"),
            ("GITHUB_REF", "refs/heads/Main"),
            ("GITHUB_WORKFLOW_REF", HANDOFF.WORKFLOW_REF.replace("watcher", "other")),
            ("GITHUB_EVENT_NAME", "pull_request"),
            ("DRY_RUN", "true"),
            ("PREPARE_RESULT", "failure"),
        ):
            with self.subTest(key=key), patch.dict(self.env, {key: value}):
                with self.assertRaisesRegex(HANDOFF.HandoffError, "Untrusted publication"):
                    self.publish()
                self.assert_no_writes()

    def test_valid_workflow_dispatch_attempt_passes_preflight(self) -> None:
        self.env["GITHUB_EVENT_NAME"] = "workflow_dispatch"
        self.assertEqual(HANDOFF.publication_preflight(self.checkout, self.env), self.ready)

    def test_missing_stale_or_incomplete_readiness_blocks_publication(self) -> None:
        for field, value, message in (
            ("attempt", "1", "entire workflow"),
            ("run", "123", "entire workflow"),
            ("artifact", "not-an-id", "invalid run"),
            ("digest", "", "evidence is missing"),
            ("base", "f" * 40, "readiness base"),
            ("gates", {"discovery": "passed"}, "evidence is missing"),
            ("source", {}, "source evidence is incomplete"),
        ):
            with self.subTest(field=field):
                self.env["PREPARE_READY"] = json.dumps({**self.ready, field: value})
                with self.assertRaisesRegex(HANDOFF.HandoffError, message):
                    self.publish()
                self.assert_no_writes()
        self.env.pop("PREPARE_READY")
        with self.assertRaisesRegex(HANDOFF.HandoffError, "readiness is missing"):
            self.publish()
        self.assert_no_writes()

    def test_wrong_artifact_bytes_fail_upload_digest_check(self) -> None:
        self.zip.write_bytes(self.zip.read_bytes() + b"other artifact")
        with self.assertRaisesRegex(HANDOFF.HandoffError, "SHA256 differs"):
            self.publish()
        self.assert_no_writes()
        self.assertEqual(self.request_count, 0)

    def test_unexpected_artifact_path_is_rejected_even_with_valid_digest(self) -> None:
        self.pack_artifact(("../unexpected", b"extra"))
        with self.assertRaisesRegex(HANDOFF.HandoffError, "unexpected path"):
            self.publish()
        self.assertFalse((self.root / "unexpected").exists())
        self.assert_no_writes()

    def test_unexpected_artifact_type_is_rejected_even_with_valid_digest(self) -> None:
        path = self.artifact / "README.md"
        path.unlink()
        entry = zipfile.ZipInfo(path.name)
        entry.create_system = 3
        entry.external_attr = 0o120777 << 16
        # Preserve valid readiness while swapping in the malicious ZIP and its digest.
        with zipfile.ZipFile(self.zip, "w") as archive:
            archive.writestr(entry, "other-file")
        self.ready["digest"] = hashlib.sha256(self.zip.read_bytes()).hexdigest()
        self.env["PREPARE_READY"] = json.dumps(self.ready)
        with self.assertRaisesRegex(HANDOFF.HandoffError, "unexpected path, type"):
            self.publish()
        self.assert_no_writes()

    def test_handoff_base_must_equal_the_run_base(self) -> None:
        path = self.artifact / "release.json"
        payload = json.loads(path.read_text())
        payload["base_commit"] = "a" * 40
        path.write_text(json.dumps(payload))
        with zipfile.ZipFile(self.zip, "w") as archive:
            for item in self.artifact.rglob("*"):
                if item.is_file():
                    archive.write(item, item.relative_to(self.artifact))
        self.ready["digest"] = hashlib.sha256(self.zip.read_bytes()).hexdigest()
        self.env["PREPARE_READY"] = json.dumps(self.ready)
        with self.assertRaisesRegex(HANDOFF.HandoffError, "Handoff base differs"):
            self.publish()
        self.assert_no_writes()

    def test_modified_readme_with_valid_digest_and_versions_is_rejected(self) -> None:
        path = self.artifact / "README.md"
        path.write_text(path.read_text() + "\nUnreviewed content\n")
        self.pack_artifact()
        with self.assertRaisesRegex(HANDOFF.HandoffError, "expected generated README"):
            self.publish()
        self.assert_no_writes()

    def test_stage_rejects_an_extra_operation_inserted_after_validation(self) -> None:
        stage = HANDOFF.stage_candidate

        def extra_operation(root, artifact, handoff):
            (root / "extra").write_text("extra\n")
            HANDOFF.git(root, "add", "extra")
            return stage(root, artifact, handoff)

        with patch.object(HANDOFF, "stage_candidate", side_effect=extra_operation):
            with self.assertRaisesRegex(HANDOFF.HandoffError, "Staged paths or operations"):
                self.publish()
        self.assert_no_writes()

    def test_stale_checkout_is_rejected_before_local_application(self) -> None:
        HANDOFF.git(self.checkout, "commit", "--allow-empty", "-qm", "Different checkout base")
        with self.assertRaisesRegex(HANDOFF.HandoffError, "Checkout HEAD"):
            self.publish()
        self.assert_no_writes()

    def test_stale_remote_base_blocks_push(self) -> None:
        def move_main():
            if self.request_count == 1:
                subprocess.run(
                    ["git", "-C", str(self.checkout), "push", "origin", "HEAD:refs/heads/main"],
                    check=True, capture_output=True,
                )

        self.on_request = move_main
        with self.assertRaisesRegex(HANDOFF.HandoffError, "Remote main differs"):
            self.publish()
        self.assert_no_writes()

    def test_malformed_remote_ref_blocks_push(self) -> None:
        git = HANDOFF.git

        def malformed_main(root, *args, **kwargs):
            if args == ("ls-remote", "--refs", "origin", HANDOFF.MAIN_REF):
                return b"invalid remote output\n"
            return git(root, *args, **kwargs)

        with patch.object(HANDOFF, "git", side_effect=malformed_main):
            with self.assertRaisesRegex(HANDOFF.HandoffError, "Remote returned inconclusive state"):
                self.publish()
        self.assert_no_writes()

    def test_wrong_source_commit_in_readiness_blocks_publication(self) -> None:
        self.ready["source"]["commit"] = "a" * 40
        self.env["PREPARE_READY"] = json.dumps(self.ready)
        with self.assertRaisesRegex(HANDOFF.HandoffError, "source evidence differs"):
            self.publish()
        self.assert_no_writes()

    def test_modified_manifest_with_valid_artifact_digest_blocks_publication(self) -> None:
        manifest = self.artifact / HANDOFF.MANIFEST_PATH
        manifest.write_text(manifest.read_text().replace(" 42 ", " 43 "))
        self.pack_artifact()
        with self.assertRaisesRegex(HANDOFF.HandoffError, "DIST size differs"):
            self.publish()
        self.assert_no_writes()

    def test_incomplete_pr_search_blocks_push(self) -> None:
        self.search["incomplete_results"] = True
        with self.assertRaisesRegex(HANDOFF.WatcherError, "incomplete or malformed"):
            self.publish()
        self.assert_no_writes()

    def test_open_title_conflict_blocks_push(self) -> None:
        self.search.update(total_count=1, items=[{"state": "open", "pull_request": {"url": "example"}}])
        with self.assertRaisesRegex(HANDOFF.HandoffError, "open release PR"):
            self.publish()
        self.assert_no_writes()

    def test_open_exact_head_conflict_blocks_push(self) -> None:
        self.pull_requests.append(self.pr())
        with self.assertRaisesRegex(HANDOFF.HandoffError, "exact candidate branch"):
            self.publish()
        self.assert_no_writes()

    def test_closed_exact_head_conflict_blocks_push(self) -> None:
        self.pull_requests.append(self.pr(state="closed"))
        with self.assertRaisesRegex(HANDOFF.HandoffError, "prior closed PR"):
            self.publish()
        self.assert_no_writes()

    def test_existing_different_branch_blocks_push(self) -> None:
        self.set_remote_ref(self.candidate_ref, self.base)
        with self.assertRaisesRegex(HANDOFF.HandoffError, "Candidate branch already exists"):
            self.publish()
        self.assertEqual(self.remote_head(), self.base)
        self.assert_no_writes()

    def test_existing_identical_branch_blocks_push(self) -> None:
        def existing_branch():
            if self.request_count == 1:
                subprocess.run(
                    ["git", "-C", str(self.checkout), "push", "origin", f"HEAD:{self.candidate_ref}"],
                    check=True, capture_output=True,
                )

        self.on_request = existing_branch
        with self.assertRaisesRegex(HANDOFF.HandoffError, "Candidate branch already exists"):
            self.publish()
        self.assertEqual(self.remote_head(), self.head())
        self.assert_no_writes()

    def assert_unconfirmed_push_report(self, observed_head: str) -> str:
        self.assertEqual(self.pr_requests, [])
        self.assertEqual(sum(args[0] == "push" for args in self.git_calls), 1)
        summary = self.summary.read_text()
        self.assertIn("Candidate branch push failed or was inconclusive", summary)
        self.assertIn("Branch creation by this run: unconfirmed.", summary)
        self.assertIn(f"Remote branch at read-back: {self.candidate_ref} at {observed_head}", summary)
        self.assertIn("Draft PR request: not run.", summary)
        self.assertIn("Owner recovery", summary)
        self.assertNotIn("Last confirmed write: created branch", summary)
        return summary

    def test_concurrent_branch_creation_is_rejected_by_real_lease(self) -> None:
        self.before_push = lambda: self.set_remote_ref(self.candidate_ref, self.base)
        with self.assertRaisesRegex(HANDOFF.HandoffError, "Git push failed"):
            self.publish()
        self.assertEqual(self.remote_head(), self.base)
        summary = self.assert_unconfirmed_push_report(self.base)
        self.assertIn("Exact-head PR state: none observed at read-back", summary)

    def test_real_up_to_date_push_does_not_authorize_pr(self) -> None:
        self.before_push = lambda: subprocess.run(
            ["git", "-C", str(self.checkout), "push", "origin", f"HEAD:{self.candidate_ref}"],
            check=True, capture_output=True,
        )
        with self.assertRaisesRegex(HANDOFF.HandoffError, "did not confirm creation"):
            self.publish()
        self.assertEqual(self.remote_head(), self.head())
        summary = self.assert_unconfirmed_push_report(self.head())
        self.assertIn("Exact-head PR state: none observed at read-back", summary)

    def test_failed_push_reads_back_created_branch_without_authorizing_pr(self) -> None:
        git = HANDOFF.git

        def lose_push_confirmation(root, *args, **kwargs):
            output = git(root, *args, **kwargs)
            if args[0] == "push":
                self.pull_requests.append(self.pr(state="closed"))
                raise HANDOFF.HandoffError("Git push failed after remote accepted the ref.")
            return output

        with patch.object(HANDOFF, "git", side_effect=lose_push_confirmation):
            with self.assertRaisesRegex(HANDOFF.HandoffError, "Git push failed after remote accepted"):
                self.publish()
        self.assertEqual(self.remote_head(), self.head())
        summary = self.assert_unconfirmed_push_report(self.head())
        self.assertIn("Exact-head PR state: confirmed closed", summary)
        self.assertIn("matches candidate commit", summary)

    def test_main_changes_before_pr_are_reported_after_confirmed_push(self) -> None:
        def move_base():
            if self.request_count == 4:
                self.set_remote_ref(HANDOFF.MAIN_REF, self.head())

        self.on_request = move_base
        with self.assertRaisesRegex(HANDOFF.HandoffError, "Remote main differs"):
            self.publish()
        self.assertEqual(self.remote_head(), self.head())
        self.assertEqual(self.pr_requests, [])
        self.assertIn("Draft PR request: not run", self.summary.read_text())

    def test_candidate_changes_before_pr_are_reported(self) -> None:
        def move_head():
            if self.request_count == 4:
                self.set_remote_ref(self.candidate_ref, self.base)

        self.on_request = move_head
        with self.assertRaisesRegex(HANDOFF.HandoffError, "candidate head changed"):
            self.publish()
        self.assertEqual(self.remote_head(), self.base)
        self.assertEqual(self.pr_requests, [])
        self.assertIn("Last confirmed write: created branch", self.summary.read_text())

    def test_post_failure_leaves_confirmed_branch_and_reports_unknown_pr(self) -> None:
        self.post_result = HANDOFF.WatcherError("GitHub API request failed with HTTP 500.")
        with self.assertRaisesRegex(HANDOFF.WatcherError, "HTTP 500"):
            self.publish()
        self.assertEqual(self.remote_head(), self.head())
        self.assertEqual(len(self.pr_requests), 1)
        self.assertIn("PR state: UNKNOWN", self.summary.read_text())
        self.assertIn("closed-PR conflict separately", self.summary.read_text())

    def test_ambiguous_post_with_created_pr_reports_confirmed_readback(self) -> None:
        def post_creates_pr():
            if self.request_count == 7:
                self.pull_requests.append(self.pr())

        self.on_request = post_creates_pr
        self.post_result = {"html_url": "https://example.invalid/pull/18"}
        with self.assertRaisesRegex(HANDOFF.WatcherError, "ambiguous Draft PR"):
            self.publish()
        self.assertEqual(self.remote_head(), self.head())
        self.assertEqual(len(self.pr_requests), 1)
        self.assertIn("confirmed open", self.summary.read_text())
