#!/usr/bin/env python3
"""Publish a verified Noctalia candidate as a Draft PR."""

from __future__ import annotations

import argparse
import base64
import os
import re
import sys
from collections.abc import Mapping
from pathlib import Path

from overlay_policy import OverlayPolicyError, version_text
from release_handoff import (
    MANIFEST_PATH,
    HandoffError,
    ReleaseHandoff,
    append_summary,
    git,
    load_prepare_readiness,
    parse_commit,
    unpack_verified_artifact,
    validate_and_apply_candidate,
    verify_checkout,
    version_path,
)
from watch_noctalia_release import GitHubClient, WatcherError


REPOSITORY = "vovanbl411/noctalia-overlay"
REMOTE_URL = f"https://github.com/{REPOSITORY}.git"
MAIN_REF = "refs/heads/main"
WORKFLOW_REF = f"{REPOSITORY}/.github/workflows/noctalia-release-watcher.yml@{MAIN_REF}"


def publication_preflight(root: Path, env: Mapping[str, str]) -> dict:
    expected = {
        "GITHUB_REPOSITORY": REPOSITORY,
        "GITHUB_REF": MAIN_REF,
        "GITHUB_WORKFLOW_REF": WORKFLOW_REF,
        "DRY_RUN": "false",
        "PREPARE_RESULT": "success",
    }
    if any(env.get(key) != value for key, value in expected.items()) or env.get("GITHUB_EVENT_NAME") not in {
        "schedule", "workflow_dispatch",
    }:
        raise HandoffError("Untrusted publication context or Prepare did not succeed.")
    ready = load_prepare_readiness(env)
    verify_checkout(root, ready["base"])
    return ready


def stage_candidate(root: Path, artifact: Path, handoff: ReleaseHandoff) -> bytes:
    candidate_path = version_path(handoff.candidate)
    changes = {
        "README.md": "M",
        "README.en.md": "M",
        MANIFEST_PATH: "M",
        candidate_path: "A",
        version_path(handoff.removed): "D",
    }
    git(root, "add", "--", *changes)
    staged = git(root, "diff", "--cached", "--no-renames", "--name-status", "-z")
    entries = staged.split(b"\0")
    expected = {(status.encode(), path.encode()) for path, status in changes.items()}
    if entries[-1] != b"" or len(entries) != 2 * len(expected) + 1 or set(
        zip(entries[::2], entries[1::2])
    ) != expected:
        raise HandoffError("Staged paths or operations differ from the intended rotation.")
    git(root, "diff", "--cached", "--check")
    for path, status in changes.items():
        if status == "D":
            continue
        if git(root, "show", f":{path}") != (artifact / path).read_bytes():
            raise HandoffError(f"Staged bytes differ from verified handoff for {path}.")
        if not git(root, "ls-files", "--stage", "--", path).startswith(b"100644 "):
            raise HandoffError(f"Unexpected staged mode for {path}.")
    return git(root, "write-tree")


def remote_ref(root: Path, ref: str, git_env: dict[str, str] | None) -> str | None:
    result = git(root, "ls-remote", "--refs", "origin", ref, env=git_env)
    if not result:
        return None
    match = re.fullmatch(rb"([0-9a-f]{40})\t" + re.escape(ref.encode()) + rb"\n", result)
    if not match:
        raise HandoffError(f"Remote returned inconclusive state for {ref}.")
    return match[1].decode("ascii")


def verify_remote_base(root: Path, base: str, git_env: dict[str, str] | None) -> None:
    if remote_ref(root, MAIN_REF, git_env) != base:
        raise HandoffError("Remote main differs from the validated handoff base.")


def check_pr_conflicts(client: GitHubClient, handoff: ReleaseHandoff) -> None:
    if client.has_open_pull_request_matching_title(handoff.pull_request_title):
        raise HandoffError("An open release PR conflicts with this candidate.")
    if client.pull_requests_for_head(handoff.branch, state="all"):
        raise HandoffError("An open or prior closed PR for this exact candidate branch requires owner review.")


def create_remote_branch(root: Path, ref: str, git_env: dict[str, str] | None) -> None:
    output = git(
        root, "push", "--porcelain", f"--force-with-lease={ref}:", "origin", f"HEAD:{ref}",
        env=git_env,
    )
    statuses = [line for line in output.splitlines() if b"\t" in line]
    if statuses != [f"*\tHEAD:{ref}\t[new branch]".encode()]:
        raise HandoffError("Push did not confirm creation of a new candidate ref.")


def report_partial_publication(
    root: Path, ref: str, client: GitHubClient, handoff: ReleaseHandoff,
    commit: str, git_env: dict[str, str] | None, summary: Path, *, attempted_pr: bool,
) -> None:
    try:
        head = remote_ref(root, ref, git_env)
        branch_state = f"{ref} at {head}" if head else "confirmed absent at read-back"
    except (HandoffError, OSError):
        branch_state = "UNKNOWN"
    try:
        prs = client.pull_requests_for_head(handoff.branch)
        if prs:
            pr_state = "; ".join(
                f"confirmed {pr['state']} {pr['html_url']}"
                + (" (matches candidate commit)" if pr["head"]["sha"] == commit else " (different head commit)")
                for pr in prs
            )
        elif attempted_pr:
            pr_state = "UNKNOWN after the failed submission (none observed at read-back)"
        else:
            pr_state = "none observed at read-back"
    except WatcherError:
        pr_state = "UNKNOWN"
    append_summary(summary, f"- Remote branch at read-back: {branch_state}")
    append_summary(summary, f"- Exact-head PR state: {pr_state}")
    append_summary(
        summary,
        "- Owner recovery: verify branch ownership, the candidate commit, current base, and "
        "PR history before recovering the Draft PR manually. Resolve any closed-PR conflict separately. "
        "If abandoning the candidate, remove only a confirmed orphan you own.",
    )


def publish_candidate(
    root: Path, download: Path, destination: Path, summary: Path,
    env: Mapping[str, str], client: GitHubClient, *,
    remote_url: str = REMOTE_URL, git_env: dict[str, str] | None = None,
) -> None:
    ready = publication_preflight(root, env)
    for args in (("get-url", "--all", "origin"), ("get-url", "--push", "--all", "origin")):
        if git(root, "remote", *args, env=git_env) != (remote_url + "\n").encode():
            raise HandoffError("Origin is not the intended release publication remote.")
    handoff = unpack_verified_artifact(download, destination, ready)
    validate_and_apply_candidate(root, destination, handoff, ready)
    tree = stage_candidate(root, destination, handoff)
    git(
        root, "-c", "user.name=github-actions[bot]", "-c",
        "user.email=41898282+github-actions[bot]@users.noreply.github.com",
        "commit", "-m", f"update: Noctalia {handoff.release_tag}",
    )
    commit = parse_commit(git(root, "rev-parse", "HEAD").decode().strip(), "published commit")
    if git(root, "rev-parse", "HEAD^{tree}", "HEAD^") != tree + (handoff.base_commit + "\n").encode():
        raise HandoffError("Local commit does not match the verified tree and base.")

    # validate_handoff constructed this ref from the parsed, stable candidate version.
    ref = f"refs/heads/{handoff.branch}"
    check_pr_conflicts(client, handoff)
    if remote_ref(root, ref, git_env) is not None:
        raise HandoffError("Candidate branch already exists. Owner review is required.")
    verify_remote_base(root, handoff.base_commit, git_env)
    branch_created = False
    attempted_pr = False
    try:
        create_remote_branch(root, ref, git_env)
        branch_created = True
        append_summary(summary, f"- Last confirmed write: created branch `{ref}` at `{commit}`.")
        check_pr_conflicts(client, handoff)
        verify_remote_base(root, handoff.base_commit, git_env)
        if remote_ref(root, ref, git_env) != commit:
            raise HandoffError("Remote candidate head changed before Draft PR creation.")
        attempted_pr = True
        url = client.create_draft(
            handoff.pull_request_title, handoff.branch,
            draft_pull_request_body(handoff, ready), commit, handoff.base_commit,
        )
    except (HandoffError, WatcherError, OSError) as error:
        if not branch_created:
            detail = f" {error}" if isinstance(error, HandoffError) else ""
            append_summary(summary, f"- Candidate branch push failed or was inconclusive.{detail}")
            append_summary(summary, "- Branch creation by this run: unconfirmed.")
        if not attempted_pr:
            append_summary(summary, "- Draft PR request: not run.")
        report_partial_publication(
            root, ref, client, handoff, commit, git_env, summary, attempted_pr=attempted_pr
        )
        raise
    append_summary(summary, f"- Draft PR confirmed: {url}")


def authenticated_git_environment(token: str) -> dict[str, str]:
    credentials = base64.b64encode(f"x-access-token:{token}".encode()).decode("ascii")
    return {
        **os.environ,
        "LC_ALL": "C",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
        "GIT_CONFIG_VALUE_0": f"Authorization: Basic {credentials}",
    }


def draft_pull_request_body(handoff: ReleaseHandoff, readiness: dict) -> str:
    """Render the release evidence and manual-review checklist."""
    source = readiness["source"]
    run_url = f"https://github.com/{REPOSITORY}/actions/runs/{readiness['run']}"
    return "\n".join(
        (
            f"# Noctalia release: [{handoff.release_tag}]({handoff.release_url})",
            "",
            "## Overlay rotation",
            "",
            f"- Removed: `{version_text(handoff.removed)}`",
            f"- Fallback: `{version_text(handoff.fallback)}`",
            f"- Candidate: `{version_text(handoff.candidate)}`",
            "",
            "## Automated checks",
            "",
            "- Stable release format",
            "- Manifest generated",
            "- Exactly two stable ebuilds",
            "- Python unit tests",
            "- Stable release and exact commit VERSION verified",
            "- `pkgcheck scan`",
            "",
            f"- Workflow run: [{readiness['run']}]({run_url})",
            f"- Prepare attempt: `{readiness['attempt']}`",
            f"- Validated base SHA: `{readiness['base']}`",
            f"- Canonical source archive: {source['url']}",
            f"- Archive size: {source['archive']['size']} bytes",
            f"- BLAKE2B: `{source['archive']['blake2b']}`",
            f"- SHA512: `{source['archive']['sha512']}`",
            "- Archive commit ID, gzip/tar integrity and both Manifest entries: passed",
            "- Full source build and runtime checks: not run",
            "",
            "## Upstream provenance",
            "",
            f"- Tag type: {handoff.provenance.tag_type}",
            f"- Target commit: `{handoff.provenance.target_commit_sha}`",
            "- GitHub tag signature verification: "
            f"{'verified' if handoff.provenance.signature_verified else 'not applicable' if handoff.provenance.tag_type == 'lightweight' else 'not verified'}",
            f"- Verification reason: `{handoff.provenance.verification_reason}`",
            *(
                (f"- Tag object: `{handoff.provenance.tag_object_sha}`",)
                if handoff.provenance.tag_object_sha is not None
                else ()
            ),
            *(
                (f"- Verified at: `{handoff.provenance.verified_at}`",)
                if handoff.provenance.verified_at is not None
                else ()
            ),
            "",
            "## Upstream packaging changes",
            "",
            f"- **PACKAGING.md:** {handoff.packaging_changes['packaging_md']}",
            f"- `meson.build`: {handoff.packaging_changes['meson_build']}",
            f"- `meson_options.txt`: {handoff.packaging_changes['meson_options']}",
            "",
            "## Manual verification",
            "",
            "- [ ] Review upstream release notes.",
            "- [ ] Review upstream provenance/signing identity if anything looks unusual.",
            "- [ ] Review `PACKAGING.md` changes and dependency changes.",
            "- [ ] Run `emerge -pv gui-apps/noctalia`.",
            "- [ ] Install the candidate Noctalia version.",
            "- [ ] Test a normal Niri session, including the panel and notifications.",
            "- [ ] Test network integration, audio, and the lock screen.",
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    preflight_parser = subparsers.add_parser("preflight")
    preflight_parser.add_argument("--repository-root", type=Path, required=True)

    publish_parser = subparsers.add_parser("publish")
    publish_parser.add_argument("--repository-root", type=Path, required=True)
    publish_parser.add_argument("--download", type=Path, required=True)
    publish_parser.add_argument("--handoff-root", type=Path, required=True)

    args = parser.parse_args()
    try:
        if args.command == "preflight":
            ready = publication_preflight(args.repository_root, os.environ)
            with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
                print(f"artifact_id={ready['artifact']}", file=output)
        elif args.command == "publish":
            token = os.environ["GH_TOKEN"]
            publish_candidate(
                args.repository_root, args.download, args.handoff_root,
                Path(os.environ["GITHUB_STEP_SUMMARY"]), os.environ,
                GitHubClient(token, REPOSITORY), git_env=authenticated_git_environment(token),
            )
        return 0
    except (OverlayPolicyError, WatcherError, OSError, UnicodeError) as error:
        print(f"Release publication error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
