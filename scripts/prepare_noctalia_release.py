#!/usr/bin/env python3
"""Prepare a Noctalia release rotation without talking to GitHub."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

SCRIPT_DIRECTORY = str(Path(__file__).parent)
if SCRIPT_DIRECTORY not in sys.path:
    sys.path.insert(0, SCRIPT_DIRECTORY)

from overlay_policy import (
    OverlayPolicyError,
    Version,
    overlay_state,
    parse_version,
    update_readme_versions,
    validate_overlay,
    version_text,
)


@dataclass(frozen=True)
class Rotation:
    """The ebuild changes required to make a release candidate."""

    removed: Version
    fallback: Version
    candidate: Version
    source_ebuild: Path
    target_ebuild: Path


@dataclass(frozen=True)
class PrepareResult:
    """The outcome of preparing one release."""

    outcome: str
    rotation: Rotation | None = None


def plan_rotation(repository_root: Path, release_version: Version) -> Rotation | None:
    """Return the safe two-version rotation, or None when already packaged."""
    state = validate_overlay(repository_root)
    if release_version <= state.current.version:
        return None
    target = state.current.path.with_name(
        f"noctalia-{version_text(release_version)}.ebuild"
    )
    if target.exists():
        raise OverlayPolicyError(f"Target ebuild already exists: {target.name}.")
    return Rotation(
        removed=state.fallback.version,
        fallback=state.current.version,
        candidate=release_version,
        source_ebuild=state.current.path,
        target_ebuild=target,
    )


def prepare_release(
    repository_root: Path, release_version: Version, *, dry_run: bool
) -> PrepareResult:
    """Copy current, remove old fallback, and update README as one rotation."""
    rotation = plan_rotation(repository_root, release_version)
    if rotation is None:
        return PrepareResult("up-to-date")
    if dry_run:
        return PrepareResult("dry-run", rotation)

    shutil.copy2(rotation.source_ebuild, rotation.target_ebuild)
    rotation.source_ebuild.with_name(
        f"noctalia-{version_text(rotation.removed)}.ebuild"
    ).unlink()
    state = overlay_state(repository_root)
    update_readme_versions(repository_root / "README.md", state)
    validate_overlay(repository_root)
    return PrepareResult("prepared", rotation)


def summary_lines(result: PrepareResult) -> tuple[str, ...]:
    """Return GitHub Step Summary lines for a prepared or dry-run rotation."""
    if result.rotation is None:
        return ("## Noctalia release preparation", "", "- Result: `up-to-date`")
    rotation = result.rotation
    return (
        "## Noctalia release preparation",
        "",
        f"- Result: `{result.outcome}`",
        f"- Removed fallback: `{version_text(rotation.removed)}`",
        f"- Retained fallback: `{version_text(rotation.fallback)}`",
        f"- Candidate: `{version_text(rotation.candidate)}`",
    )


def draft_pull_request_body(
    release_tag: str,
    release_url: str,
    rotation: Rotation,
    packaging_changes: dict[str, str],
) -> str:
    """Render the Draft PR checklist; all inspection items remain manual."""
    return "\n".join(
        (
            f"# Noctalia release: [{release_tag}]({release_url})",
            "",
            "## Overlay rotation",
            "",
            f"- Removed: `{version_text(rotation.removed)}`",
            f"- Fallback: `{version_text(rotation.fallback)}`",
            f"- Candidate: `{version_text(rotation.candidate)}`",
            "",
            "## Automated checks",
            "",
            "- Stable release format",
            "- Manifest generated",
            "- Exactly two stable ebuilds",
            "- Python unit tests",
            "- `pkgcheck scan`",
            "",
            "## Upstream packaging changes",
            "",
            f"- **PACKAGING.md:** {packaging_changes['packaging_md']}",
            f"- `meson.build`: {packaging_changes['meson_build']}",
            f"- `meson_options.txt`: {packaging_changes['meson_options']}",
            "",
            "## Manual verification",
            "",
            "- [ ] Review upstream release notes.",
            "- [ ] Verify the upstream tag and signature where applicable.",
            "- [ ] Review `PACKAGING.md` changes and dependency changes.",
            "- [ ] Run `emerge -pv gui-apps/noctalia`.",
            "- [ ] Install the candidate Noctalia version.",
            "- [ ] Test a normal Niri session, including the panel and notifications.",
            "- [ ] Test network integration, audio, and the lock screen.",
        )
    )


def write_pull_request_request(
    path: Path,
    *,
    release_tag: str,
    release_url: str,
    rotation: Rotation,
    packaging_changes: dict[str, str],
) -> None:
    """Write the GitHub REST payload for the deterministic draft branch."""
    branch = f"automation/noctalia-{release_tag}"
    payload = {
        "base": "main",
        "body": draft_pull_request_body(
            release_tag, release_url, rotation, packaging_changes
        ),
        "draft": True,
        "head": branch,
        "title": f"[release-bump] Noctalia {release_tag}",
    }
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


def release_details_from_watch_result(path: Path, tag: str) -> tuple[str, dict[str, str]]:
    """Validate the watcher's JSON handoff before using it in a PR body."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise OverlayPolicyError("Unable to read the watcher result.") from error
    if not isinstance(payload, dict):
        raise OverlayPolicyError("Invalid watcher result.")
    release = payload.get("release")
    changes = payload.get("packaging_changes")
    if not isinstance(release, dict) or release.get("tag") != tag:
        raise OverlayPolicyError("Watcher result does not match the release tag.")
    release_url = release.get("url")
    expected_keys = {"packaging_md", "meson_build", "meson_options"}
    parsed_url = urlparse(release_url) if isinstance(release_url, str) else None
    if (
        not isinstance(release_url, str)
        or parsed_url is None
        or parsed_url.scheme != "https"
        or parsed_url.netloc != "github.com"
        or not isinstance(changes, dict)
    ):
        raise OverlayPolicyError("Watcher result is missing release details.")
    if set(changes) != expected_keys or not all(
        isinstance(value, str) for value in changes.values()
    ):
        raise OverlayPolicyError("Watcher result has invalid packaging changes.")
    return release_url, changes


def write_github_output(path: Path, result: PrepareResult) -> None:
    """Write simple workflow outputs without exposing untrusted values to shell."""
    has_rotation = result.rotation is not None
    lines = (
        f"prepared={'true' if has_rotation else 'false'}",
        f"outcome={result.outcome}",
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--release-tag", required=True)
    parser.add_argument("--watch-result", type=Path)
    parser.add_argument("--pull-request-request", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--github-output", type=Path)
    parser.add_argument("--summary", type=Path)
    args = parser.parse_args()

    try:
        tag = args.release_tag
        if not tag.startswith("v"):
            raise OverlayPolicyError(f"Not a stable Noctalia tag: {tag}.")
        result = prepare_release(
            args.repository_root, parse_version(tag[1:]), dry_run=args.dry_run
        )
        if args.github_output is not None:
            write_github_output(args.github_output, result)
        if args.summary is not None:
            with args.summary.open("a", encoding="utf-8") as summary:
                summary.write("\n".join(summary_lines(result)) + "\n")
        if args.pull_request_request is not None and result.rotation is not None:
            if args.watch_result is None:
                raise OverlayPolicyError("A pull request request needs a watcher result.")
            release_url, changes = release_details_from_watch_result(args.watch_result, tag)
            write_pull_request_request(
                args.pull_request_request,
                release_tag=tag,
                release_url=release_url,
                rotation=result.rotation,
                packaging_changes=changes,
            )
        return 0
    except OverlayPolicyError as error:
        print(f"Release preparation error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
