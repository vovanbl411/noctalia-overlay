from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch

from test_release_handoff import (
    HANDOFF,
    IsolatedGitTests,
    manifest_line,
    write_overlay,
    write_source_evidence,
    write_watcher_result,
)

import publish_noctalia_release as PUBLISH
from prepare_noctalia_release import prepare_release


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
        write_source_evidence(self.evidence)
        self.env = {
            "GITHUB_RUN_ID": "124", "GITHUB_RUN_ATTEMPT": "2", "GITHUB_SHA": self.base,
            "GITHUB_REPOSITORY": PUBLISH.REPOSITORY, "GITHUB_REF": PUBLISH.MAIN_REF,
            "GITHUB_WORKFLOW_REF": PUBLISH.WORKFLOW_REF, "GITHUB_EVENT_NAME": "schedule",
            "DRY_RUN": "false", "PREPARE_RESULT": "success", "UPLOADED_ARTIFACT_ID": "321",
        }
        self.download.mkdir()
        self.zip = self.download / "artifact.zip"
        self.pack_artifact()
        self.client = PUBLISH.GitHubClient("fake-token", PUBLISH.REPOSITORY)
        self.pr_requests = []
        self.pull_requests = []
        self.search = {"total_count": 0, "incomplete_results": False, "items": []}
        self.post_result = None
        self.git_calls = []
        self.api_calls = []
        self.on_request = lambda operation: None
        self.before_push = lambda: None

    def head(self) -> str:
        return HANDOFF.git(self.checkout, "rev-parse", "HEAD").decode().strip()

    def remote_head(self) -> str | None:
        return PUBLISH.remote_ref(self.checkout, self.candidate_ref, None)

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
        repo = {"full_name": PUBLISH.REPOSITORY}
        return {
            "number": 18, "html_url": f"https://github.com/{PUBLISH.REPOSITORY}/pull/18",
            "title": "[release-bump] Noctalia v5.2.0", "state": state, "draft": True,
            "head": {"ref": self.branch, "sha": head or self.head(), "repo": repo},
            "base": {"ref": "main", "sha": self.base, "repo": repo},
        }

    def request(self, url, *, query=None, allow_not_found=False, data=None):
        if url.startswith("https://api.github.com/search/issues?"):
            self.api_calls.append("title search")
            self.on_request("title search")
            return self.search
        self.assertEqual(url, f"https://api.github.com/repos/{PUBLISH.REPOSITORY}/pulls")
        if data is not None:
            self.api_calls.append("create draft")
            self.on_request("create draft")
            self.pr_requests.append(data)
            if isinstance(self.post_result, Exception):
                raise self.post_result
            return self.pr() if self.post_result is None else self.post_result
        self.assertEqual(query["head"], f"vovanbl411:{self.branch}")
        state = query["state"]
        self.api_calls.append(f"exact head: {state}")
        self.on_request("exact head")
        return [p for p in self.pull_requests if state in ("all", p["state"])]

    def publish(self) -> None:
        git = PUBLISH.git

        def trace(root, *args, **kwargs):
            self.git_calls.append(args)
            if args[0] == "push":
                self.before_push()
            return git(root, *args, **kwargs)

        with (
            patch.object(HANDOFF, "git", side_effect=trace),
            patch.object(PUBLISH, "git", side_effect=trace),
            patch.object(self.client, "_request_payload", side_effect=self.request),
        ):
            PUBLISH.publish_candidate(
                self.checkout, self.download, self.output, self.summary, self.env,
                self.client, remote_url=str(self.remote),
            )

    def assert_no_writes(self) -> None:
        self.assertFalse(any(args[0] == "push" for args in self.git_calls))
        self.assertEqual(self.pr_requests, [])

    def test_valid_publication_creates_candidate_and_draft(self) -> None:
        self.assertEqual(PUBLISH.publication_preflight(self.checkout, self.env)["artifact"], "321")
        self.publish()
        self.assertEqual(self.remote_head(), self.head())
        self.assertEqual(self.api_calls, [
            "title search", "exact head: all",
            "title search", "exact head: all",
            "create draft",
        ])
        self.assertEqual(len(self.pr_requests), 1)
        self.assertEqual(self.pr_requests[0]["head"], self.branch)
        self.assertEqual(self.pr_requests[0]["base"], "main")
        self.assertIs(self.pr_requests[0]["draft"], True)
        body = self.pr_requests[0]["body"]
        self.assertIn("https://github.com/vovanbl411/noctalia-overlay/actions/runs/124", body)
        self.assertIn("Prepare attempt: `2`", body)
        self.assertIn(f"Validated base SHA: `{self.base}`", body)
        self.assertIn("Archive size: 42 bytes", body)
        self.assertIn(f"BLAKE2B: `{'1' * 128}`", body)
        self.assertIn(f"SHA512: `{'2' * 128}`", body)
        self.assertIn("**PACKAGING.md:** modified", body)
        self.assertIn("`meson.build`: unchanged", body)
        self.assertIn("`meson_options.txt`: not present", body)
        self.assertIn(f"Target commit: `{'c' * 40}`", body)
        self.assertIn("- [ ] Test network integration, audio, and the lock screen.", body)
        self.assertIn("Full source build and runtime checks: not run", body)
        self.assertIn("Draft PR confirmed", self.summary.read_text())

    def test_draft_pull_request_contains_annotated_signed_provenance(self) -> None:
        handoff = HANDOFF.validate_handoff(self.artifact)

        body = PUBLISH.draft_pull_request_body(handoff, self.ready)

        self.assertIn("GitHub tag signature verification: verified", body)
        self.assertIn(f"Tag object: `{'b' * 40}`", body)
        self.assertIn(
            "Review upstream provenance/signing identity if anything looks unusual.",
            body,
        )

    def test_draft_pull_request_distinguishes_lightweight_and_unsigned_tags(self) -> None:
        metadata_path = self.artifact / "release.json"
        metadata = json.loads(metadata_path.read_text())
        metadata["provenance"].update(
            tag_type="lightweight",
            tag_object_sha=None,
            signature_verified=False,
            verification_reason="not-applicable",
            verified_at=None,
        )
        metadata_path.write_text(json.dumps(metadata))
        self.pack_artifact()
        handoff = HANDOFF.validate_handoff(self.artifact)
        body = PUBLISH.draft_pull_request_body(handoff, self.ready)
        self.assertIn("Tag type: lightweight", body)
        self.assertIn("GitHub tag signature verification: not applicable", body)
        self.assertNotIn("OpenPGP signature: verified by GitHub", body)

        metadata["provenance"].update(
            tag_type="annotated", tag_object_sha="b" * 40, verification_reason="unsigned",
        )
        metadata_path.write_text(json.dumps(metadata))
        self.pack_artifact()
        handoff = HANDOFF.validate_handoff(self.artifact)
        body = PUBLISH.draft_pull_request_body(handoff, self.ready)
        self.assertIn("GitHub tag signature verification: not verified", body)
        self.assertIn("Verification reason: `unsigned`", body)

    def test_context_is_case_sensitive_and_blocks_untrusted_runs(self) -> None:
        for key, value in (
            ("GITHUB_REPOSITORY", "Vovanbl411/noctalia-overlay"),
            ("GITHUB_REF", "refs/heads/Main"),
            ("GITHUB_WORKFLOW_REF", PUBLISH.WORKFLOW_REF.replace("watcher", "other")),
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
        self.assertEqual(PUBLISH.publication_preflight(self.checkout, self.env), self.ready)

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
        self.assertEqual(self.api_calls, [])

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
        stage = PUBLISH.stage_candidate

        def extra_operation(root, artifact, handoff):
            (root / "extra").write_text("extra\n")
            HANDOFF.git(root, "add", "extra")
            return stage(root, artifact, handoff)

        with patch.object(PUBLISH, "stage_candidate", side_effect=extra_operation):
            with self.assertRaisesRegex(HANDOFF.HandoffError, "Staged paths or operations"):
                self.publish()
        self.assert_no_writes()

    def test_stale_checkout_is_rejected_before_local_application(self) -> None:
        HANDOFF.git(self.checkout, "commit", "--allow-empty", "-qm", "Different checkout base")
        with self.assertRaisesRegex(HANDOFF.HandoffError, "Checkout HEAD"):
            self.publish()
        self.assert_no_writes()

    def test_stale_remote_base_blocks_push(self) -> None:
        def move_main(operation):
            if operation == "title search":
                subprocess.run(
                    ["git", "-C", str(self.checkout), "push", "origin", "HEAD:refs/heads/main"],
                    check=True, capture_output=True,
                )

        self.on_request = move_main
        with self.assertRaisesRegex(HANDOFF.HandoffError, "Remote main differs"):
            self.publish()
        self.assert_no_writes()

    def test_malformed_remote_ref_blocks_push(self) -> None:
        git = PUBLISH.git

        def malformed_main(root, *args, **kwargs):
            if args == ("ls-remote", "--refs", "origin", PUBLISH.MAIN_REF):
                return b"invalid remote output\n"
            return git(root, *args, **kwargs)

        with patch.object(PUBLISH, "git", side_effect=malformed_main):
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
        with self.assertRaisesRegex(PUBLISH.WatcherError, "incomplete or malformed"):
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
        def existing_branch(operation):
            if operation == "title search":
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
        git = PUBLISH.git

        def lose_push_confirmation(root, *args, **kwargs):
            output = git(root, *args, **kwargs)
            if args[0] == "push":
                self.pull_requests.append(self.pr(state="closed"))
                raise HANDOFF.HandoffError("Git push failed after remote accepted the ref.")
            return output

        with patch.object(PUBLISH, "git", side_effect=lose_push_confirmation):
            with self.assertRaisesRegex(HANDOFF.HandoffError, "Git push failed after remote accepted"):
                self.publish()
        self.assertEqual(self.remote_head(), self.head())
        summary = self.assert_unconfirmed_push_report(self.head())
        self.assertIn("Exact-head PR state: confirmed closed", summary)
        self.assertIn("matches candidate commit", summary)

    def test_main_changes_before_pr_are_reported_after_confirmed_push(self) -> None:
        def move_base(operation):
            if operation == "title search" and self.api_calls.count("title search") == 2:
                self.set_remote_ref(PUBLISH.MAIN_REF, self.head())

        self.on_request = move_base
        with self.assertRaisesRegex(HANDOFF.HandoffError, "Remote main differs"):
            self.publish()
        self.assertEqual(self.remote_head(), self.head())
        self.assertEqual(self.pr_requests, [])
        self.assertIn("Draft PR request: not run", self.summary.read_text())

    def test_candidate_changes_before_pr_are_reported(self) -> None:
        def move_head(operation):
            if operation == "title search" and self.api_calls.count("title search") == 2:
                self.set_remote_ref(self.candidate_ref, self.base)

        self.on_request = move_head
        with self.assertRaisesRegex(HANDOFF.HandoffError, "candidate head changed"):
            self.publish()
        self.assertEqual(self.remote_head(), self.base)
        self.assertEqual(self.pr_requests, [])
        self.assertIn("Last confirmed write: created branch", self.summary.read_text())

    def test_closed_exact_head_conflict_before_draft_pr_leaves_created_branch(self) -> None:
        def report_closed_pr(operation):
            if operation == "exact head" and any(args[0] == "push" for args in self.git_calls):
                self.pull_requests = [self.pr(state="closed")]

        self.on_request = report_closed_pr
        with self.assertRaisesRegex(HANDOFF.HandoffError, "prior closed PR"):
            self.publish()
        self.assertEqual(self.remote_head(), self.head())
        self.assertEqual(self.pr_requests, [])
        self.assertIn("Draft PR request: not run", self.summary.read_text())
        self.assertIn("Exact-head PR state: confirmed closed", self.summary.read_text())

    def test_post_failure_leaves_confirmed_branch_and_reports_unknown_pr(self) -> None:
        self.post_result = PUBLISH.WatcherError("GitHub API request failed with HTTP 500.")
        with self.assertRaisesRegex(PUBLISH.WatcherError, "HTTP 500"):
            self.publish()
        self.assertEqual(self.remote_head(), self.head())
        self.assertEqual(len(self.pr_requests), 1)
        self.assertIn("PR state: UNKNOWN", self.summary.read_text())
        self.assertIn("closed-PR conflict separately", self.summary.read_text())

    def test_ambiguous_post_with_created_pr_reports_confirmed_readback(self) -> None:
        def post_creates_pr(operation):
            if operation == "create draft":
                self.pull_requests.append(self.pr())

        self.on_request = post_creates_pr
        self.post_result = {"html_url": "https://example.invalid/pull/18"}
        with self.assertRaisesRegex(PUBLISH.WatcherError, "ambiguous Draft PR"):
            self.publish()
        self.assertEqual(self.remote_head(), self.head())
        self.assertEqual(len(self.pr_requests), 1)
        self.assertIn("confirmed open", self.summary.read_text())
