#!/usr/bin/env python3
"""Prepare a Noctalia release rotation without talking to GitHub."""

from __future__ import annotations

import argparse
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

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

    # Сначала копируем ebuild: при ошибке подготовки overlay не останется с
    # единственным stable ebuild до удаления старого fallback.
    shutil.copy2(rotation.source_ebuild, rotation.target_ebuild)
    rotation.source_ebuild.with_name(
        f"noctalia-{version_text(rotation.removed)}.ebuild"
    ).unlink()
    state = overlay_state(repository_root)
    for readme_name in ("README.md", "README.en.md"):
        update_readme_versions(repository_root / readme_name, state)
    validate_overlay(repository_root)
    return PrepareResult("prepared", rotation)


def summary_lines(result: PrepareResult) -> tuple[str, ...]:
    """Return GitHub Step Summary lines for a prepared or dry-run rotation."""
    if result.rotation is None:
        return ("## Noctalia release preparation", "", "- Result: `up-to-date`")
    rotation = result.rotation
    description = (
        "Planned rotation (package files unchanged)"
        if result.outcome == "dry-run"
        else "Completed rotation"
    )
    return (
        "## Noctalia release preparation",
        "",
        f"- Result: `{result.outcome}` — {description}",
        f"- Old fallback: `{version_text(rotation.removed)}`",
        f"- Fallback after rotation: `{version_text(rotation.fallback)}`",
        f"- Candidate: `{version_text(rotation.candidate)}`",
    )


def write_github_output(path: Path, result: PrepareResult) -> None:
    """Write simple workflow outputs without exposing untrusted values to shell."""
    lines = [f"outcome={result.outcome}"]
    if result.rotation is not None:
        lines.extend(
            (
                f"removed_version={version_text(result.rotation.removed)}",
                f"fallback_version={version_text(result.rotation.fallback)}",
                f"candidate_version={version_text(result.rotation.candidate)}",
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--release-tag", required=True)
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
        return 0
    except OverlayPolicyError as error:
        print(f"Release preparation error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
