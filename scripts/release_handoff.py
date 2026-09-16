#!/usr/bin/env python3
"""Create and validate the minimal handoff from release preparation to publishing."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

SCRIPT_DIRECTORY = str(Path(__file__).parent)
if SCRIPT_DIRECTORY not in sys.path:
    sys.path.insert(0, SCRIPT_DIRECTORY)

from overlay_policy import (  # noqa: E402
    OverlayPolicyError,
    Version,
    parse_version,
    validate_overlay,
    version_text,
)


HANDOFF_SCHEMA_VERSION = 1
MAX_HANDOFF_FILE_SIZE = 1024 * 1024
MANIFEST_PATH = "gui-apps/noctalia/Manifest"
PACKAGING_CHANGE_VALUES = frozenset({"modified", "unchanged", "not present"})
PACKAGING_CHANGE_KEYS = frozenset(
    {"packaging_md", "meson_build", "meson_options"}
)
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
VERIFIED_AT_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)


class HandoffError(OverlayPolicyError):
    """Raised when a release handoff violates its trust boundary."""


@dataclass(frozen=True)
class UpstreamProvenance:
    """Minimal GitHub-verified annotated-tag metadata carried to publish."""

    tag: str
    tag_object_sha: str
    target_commit_sha: str
    signature_verified: bool
    verification_reason: str
    verified_at: str | None


@dataclass(frozen=True)
class ReleaseHandoff:
    """Validated metadata needed to publish one deterministic release PR."""

    release_tag: str
    base_commit: str
    candidate: Version
    fallback: Version
    removed: Version
    branch: str
    pull_request_title: str
    release_url: str
    provenance: UpstreamProvenance
    packaging_changes: dict[str, str]


def version_path(version: Version) -> str:
    """Return the stable ebuild path constructed from a validated version."""
    return f"gui-apps/noctalia/noctalia-{version_text(version)}.ebuild"


def handoff_file_paths(candidate: Version) -> frozenset[str]:
    """Return the exact regular files permitted in a handoff artifact."""
    return frozenset(
        {
            "release.json",
            "README.md",
            MANIFEST_PATH,
            version_path(candidate),
        }
    )


def publish_is_required(outcome: str, dry_run: bool) -> bool:
    """Return whether discovery permits a handoff and the publish job."""
    return outcome == "release-available" and not dry_run


def _lstat(path: Path, description: str) -> os.stat_result:
    try:
        return path.lstat()
    except OSError as error:
        raise HandoffError(f"Unable to inspect {description}.") from error


def _require_directory(path: Path, description: str) -> None:
    mode = _lstat(path, description).st_mode
    if not stat.S_ISDIR(mode):
        raise HandoffError(f"{description} must be a directory, not a symlink or file.")


def _require_regular_file(path: Path, description: str) -> None:
    metadata = _lstat(path, description)
    if not stat.S_ISREG(metadata.st_mode):
        raise HandoffError(
            f"{description} must be a regular file, not a symlink or special file."
        )
    if metadata.st_size > MAX_HANDOFF_FILE_SIZE:
        raise HandoffError(f"{description} exceeds the handoff size limit.")


def _collect_artifact_files(root: Path) -> tuple[dict[str, Path], set[str]]:
    """Collect files while rejecting symlinks, devices, and unexpected file types."""
    _require_directory(root, "Handoff root")
    files: dict[str, Path] = {}
    directories: set[str] = set()

    # Не доверяем распаковке архива: lstat не позволяет ссылкам и special files
    # перенаправить publish за пределы ожидаемого дерева artifact.
    def visit(directory: Path, relative: Path) -> None:
        for child in directory.iterdir():
            child_relative = relative / child.name
            child_name = child_relative.as_posix()
            metadata = _lstat(child, f"Handoff entry {child_name}")
            if stat.S_ISDIR(metadata.st_mode):
                directories.add(child_name)
                visit(child, child_relative)
            elif stat.S_ISREG(metadata.st_mode):
                if metadata.st_size > MAX_HANDOFF_FILE_SIZE:
                    raise HandoffError(f"Handoff entry {child_name} exceeds the size limit.")
                files[child_name] = child
            else:
                raise HandoffError(
                    f"Handoff entry {child_name} must be a regular file or directory."
                )

    visit(root, Path())
    return files, directories


def _parse_version(value: object, field: str) -> Version:
    if not isinstance(value, str):
        raise HandoffError(f"Handoff field {field} must be a stable version string.")
    try:
        return parse_version(value)
    except OverlayPolicyError as error:
        raise HandoffError(f"Handoff field {field} is not a stable version.") from error


def _parse_release_url(value: object, release_tag: str) -> str:
    if not isinstance(value, str):
        raise HandoffError("Handoff release_url must be a string.")
    parsed = urlparse(value)
    expected_path = f"/noctalia-dev/noctalia/releases/tag/{release_tag}"
    if (
        parsed.scheme != "https"
        or parsed.netloc != "github.com"
        or parsed.path != expected_path
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise HandoffError("Handoff release_url is not the expected upstream release URL.")
    return value


def _parse_packaging_changes(value: object) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != PACKAGING_CHANGE_KEYS:
        raise HandoffError("Handoff packaging_changes has unexpected fields.")
    if not all(
        isinstance(change, str) and change in PACKAGING_CHANGE_VALUES
        for change in value.values()
    ):
        raise HandoffError("Handoff packaging_changes has an invalid value.")
    return dict(value)


def _parse_commit(value: object, field: str) -> str:
    if not isinstance(value, str) or COMMIT_PATTERN.fullmatch(value) is None:
        raise HandoffError(f"Handoff field {field} must be a full Git commit SHA.")
    return value


def _parse_verified_at(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or VERIFIED_AT_PATTERN.fullmatch(value) is None:
        raise HandoffError("Handoff provenance verified_at is invalid.")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise HandoffError("Handoff provenance verified_at is invalid.") from error
    return value


def _parse_provenance(value: object, release_tag: str) -> UpstreamProvenance:
    expected_fields = {
        "tag",
        "tag_object_sha",
        "target_commit_sha",
        "signature_verified",
        "verification_reason",
        "verified_at",
    }
    if not isinstance(value, dict) or set(value) != expected_fields:
        raise HandoffError("Handoff provenance has unexpected fields.")
    if value["tag"] != release_tag:
        raise HandoffError("Handoff provenance tag does not match the release tag.")
    # Artifact — недоверенный input. Сохраняем точные условия принятия watcher,
    # а не считаем verification любое непустое status-значение.
    if value["signature_verified"] is not True:
        raise HandoffError("Handoff provenance signature is not verified.")
    if value["verification_reason"] != "valid":
        raise HandoffError("Handoff provenance verification reason is invalid.")
    return UpstreamProvenance(
        tag=release_tag,
        tag_object_sha=_parse_commit(value["tag_object_sha"], "tag_object_sha"),
        target_commit_sha=_parse_commit(
            value["target_commit_sha"], "target_commit_sha"
        ),
        signature_verified=True,
        verification_reason="valid",
        verified_at=_parse_verified_at(value["verified_at"]),
    )


def _reject_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise HandoffError("JSON object contains a duplicate field.")
        value[key] = item
    return value


def _load_json(path: Path, description: str) -> object:
    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise HandoffError(f"Unable to read {description}.") from error


def _parse_handoff_payload(value: object) -> ReleaseHandoff:
    expected_fields = {
        "schema_version",
        "release_tag",
        "base_commit",
        "candidate_version",
        "fallback_version",
        "removed_version",
        "branch",
        "pull_request_title",
        "release_url",
        "provenance",
        "packaging_changes",
    }
    if not isinstance(value, dict) or set(value) != expected_fields:
        raise HandoffError("Handoff release.json has unexpected fields.")
    if (
        type(value["schema_version"]) is not int
        or value["schema_version"] != HANDOFF_SCHEMA_VERSION
    ):
        raise HandoffError("Handoff release.json has an unsupported schema version.")

    candidate = _parse_version(value["candidate_version"], "candidate_version")
    fallback = _parse_version(value["fallback_version"], "fallback_version")
    removed = _parse_version(value["removed_version"], "removed_version")
    release_tag = value["release_tag"]
    if not isinstance(release_tag, str) or release_tag != f"v{version_text(candidate)}":
        raise HandoffError("Handoff release_tag does not match candidate_version.")
    if not removed < fallback < candidate:
        raise HandoffError("Handoff versions do not form a valid rotation.")

    branch = value["branch"]
    title = value["pull_request_title"]
    if branch != f"automation/noctalia-{release_tag}":
        raise HandoffError("Handoff branch is not deterministic for the release tag.")
    if title != f"[release-bump] Noctalia {release_tag}":
        raise HandoffError("Handoff pull request title is not deterministic.")
    provenance = _parse_provenance(value["provenance"], release_tag)
    return ReleaseHandoff(
        release_tag=release_tag,
        base_commit=_parse_commit(value["base_commit"], "base_commit"),
        candidate=candidate,
        fallback=fallback,
        removed=removed,
        branch=branch,
        pull_request_title=title,
        release_url=_parse_release_url(value["release_url"], release_tag),
        provenance=provenance,
        packaging_changes=_parse_packaging_changes(value["packaging_changes"]),
    )


def validate_handoff(handoff_root: Path) -> ReleaseHandoff:
    """Validate all artifact contents before publish trusts any of them."""
    files, directories = _collect_artifact_files(handoff_root)
    release_json = files.get("release.json")
    if release_json is None:
        raise HandoffError("Handoff is missing release.json.")
    payload = _load_json(release_json, "handoff release.json")
    handoff = _parse_handoff_payload(payload)
    expected_files = handoff_file_paths(handoff.candidate)
    expected_directories = {"gui-apps", "gui-apps/noctalia"}
    if set(files) != expected_files or directories != expected_directories:
        raise HandoffError("Handoff contains unexpected or missing files.")
    return handoff


def copy_sanitized_workspace(source: Path, destination: Path) -> None:
    """Copy a checkout without its Git metadata or any links into a fresh directory."""
    _require_directory(source, "Checkout workspace")
    if destination.exists():
        raise HandoffError("Sanitized workspace destination already exists.")
    destination.mkdir(parents=True)

    def copy_directory(source_directory: Path, destination_directory: Path) -> None:
        for child in source_directory.iterdir():
            # Container нужен writable overlay, но не Git metadata и не
            # credentials, которые checkout хранит в .git рабочей директории.
            if source_directory == source and child.name == ".git":
                continue
            metadata = _lstat(child, f"Checkout entry {child.name}")
            target = destination_directory / child.name
            if stat.S_ISDIR(metadata.st_mode):
                target.mkdir()
                copy_directory(child, target)
            elif stat.S_ISREG(metadata.st_mode):
                shutil.copy2(child, target)
            else:
                raise HandoffError(
                    f"Checkout entry {child.name} must be a regular file or directory."
                )

    copy_directory(source, destination)
    if (destination / ".git").exists() or (destination / ".git").is_symlink():
        raise HandoffError("Sanitized workspace contains .git.")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
    except OSError as error:
        raise HandoffError(f"Unable to hash workspace entry {path.name}.") from error
    return digest.hexdigest()


def workspace_integrity(workspace: Path) -> dict[str, dict[str, int | str]]:
    """Return a complete, link-free content and mode snapshot of a workspace."""
    _require_directory(workspace, "Sanitized workspace")
    entries: dict[str, dict[str, int | str]] = {}

    def visit(directory: Path, relative: Path) -> None:
        for child in directory.iterdir():
            child_relative = relative / child.name
            child_name = child_relative.as_posix()
            metadata = _lstat(child, f"Workspace entry {child_name}")
            mode = stat.S_IMODE(metadata.st_mode)
            if stat.S_ISDIR(metadata.st_mode):
                entries[child_name] = {"kind": "directory", "mode": mode}
                visit(child, child_relative)
            elif stat.S_ISREG(metadata.st_mode):
                entries[child_name] = {
                    "kind": "file",
                    "mode": mode,
                    "sha256": _sha256(child),
                }
            else:
                raise HandoffError(
                    f"Workspace entry {child_name} must be a regular file or directory."
                )

    visit(workspace, Path())
    if ".git" in entries or any(path.startswith(".git/") for path in entries):
        raise HandoffError("Sanitized workspace contains .git.")
    return entries


def write_workspace_integrity(workspace: Path, output: Path) -> None:
    """Write the pre-container snapshot outside the container mount."""
    output.write_text(
        json.dumps({"entries": workspace_integrity(workspace)}, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _parse_workspace_integrity(value: object) -> dict[str, dict[str, int | str]]:
    if not isinstance(value, dict) or set(value) != {"entries"}:
        raise HandoffError("Workspace integrity snapshot is invalid.")
    entries = value["entries"]
    if not isinstance(entries, dict):
        raise HandoffError("Workspace integrity entries are invalid.")
    parsed: dict[str, dict[str, int | str]] = {}
    for path, entry in entries.items():
        if not isinstance(path, str) or not isinstance(entry, dict):
            raise HandoffError("Workspace integrity entry is invalid.")
        kind = entry.get("kind")
        mode = entry.get("mode")
        expected_fields = {"kind", "mode"}
        if kind == "file":
            expected_fields.add("sha256")
        if (
            kind not in {"file", "directory"}
            or type(mode) is not int
            or mode < 0
            or mode > 0o777
            or set(entry) != expected_fields
        ):
            raise HandoffError("Workspace integrity entry is invalid.")
        if kind == "file":
            sha256 = entry["sha256"]
            if not isinstance(sha256, str) or re.fullmatch(r"[0-9a-f]{64}", sha256) is None:
                raise HandoffError("Workspace integrity file digest is invalid.")
        parsed[path] = dict(entry)
    return parsed


def verify_workspace_integrity(workspace: Path, baseline_path: Path) -> None:
    """Fail unless the container changed content or mode only for Manifest."""
    _require_regular_file(baseline_path, "Workspace integrity snapshot")
    baseline = _parse_workspace_integrity(
        _load_json(baseline_path, "workspace integrity snapshot")
    )
    after = workspace_integrity(workspace)
    if set(after) != set(baseline):
        raise HandoffError("Container added or removed a workspace entry.")
    # Manifest генерируется в container. Каждый иной path, включая содержимое и
    # mode bits, обязан совпадать со snapshot до запуска container.
    for path, before_entry in baseline.items():
        if path != MANIFEST_PATH and after[path] != before_entry:
            raise HandoffError(f"Container changed unexpected workspace entry {path}.")


def _watch_result_details(
    path: Path, release_tag: str
) -> tuple[str, UpstreamProvenance, dict[str, str]]:
    _require_regular_file(path, "Watcher result")
    payload = _load_json(path, "watcher result")
    if not isinstance(payload, dict):
        raise HandoffError("Watcher result is invalid.")
    release = payload.get("release")
    if not isinstance(release, dict) or release.get("tag") != release_tag:
        raise HandoffError("Watcher result does not match the release tag.")
    return (
        _parse_release_url(release.get("url"), release_tag),
        _parse_provenance(payload.get("provenance"), release_tag),
        _parse_packaging_changes(payload.get("packaging_changes")),
    )


def create_handoff(
    source_root: Path,
    destination: Path,
    watch_result: Path,
    *,
    release_tag: str,
    base_commit: str,
    removed: Version,
    fallback: Version,
    candidate: Version,
) -> ReleaseHandoff:
    """Create a minimal artifact from the verified, sanitized release workspace."""
    if destination.exists():
        raise HandoffError("Handoff destination already exists.")
    # Повторно проверяем дерево после container до упаковки межjob artifact.
    # Благодаря этому publish не зависит от рабочей директории prepare.
    state = validate_overlay(source_root)
    if state.fallback.version != fallback or state.current.version != candidate:
        raise HandoffError("Prepared overlay does not match the intended rotation.")
    if not removed < fallback < candidate or release_tag != f"v{version_text(candidate)}":
        raise HandoffError("Release rotation metadata is invalid.")
    release_url, provenance, packaging_changes = _watch_result_details(
        watch_result, release_tag
    )
    handoff = ReleaseHandoff(
        release_tag=release_tag,
        base_commit=_parse_commit(base_commit, "base_commit"),
        candidate=candidate,
        fallback=fallback,
        removed=removed,
        branch=f"automation/noctalia-{release_tag}",
        pull_request_title=f"[release-bump] Noctalia {release_tag}",
        release_url=release_url,
        provenance=provenance,
        packaging_changes=packaging_changes,
    )
    source_files = {
        "README.md": source_root / "README.md",
        "gui-apps/noctalia/Manifest": source_root / "gui-apps" / "noctalia" / "Manifest",
        version_path(candidate): source_root / version_path(candidate),
    }
    for relative_path, source_path in source_files.items():
        _require_regular_file(source_path, f"Prepared file {relative_path}")

    destination.mkdir(parents=True)
    (destination / "gui-apps" / "noctalia").mkdir(parents=True)
    payload = {
        "schema_version": HANDOFF_SCHEMA_VERSION,
        "release_tag": handoff.release_tag,
        "base_commit": handoff.base_commit,
        "candidate_version": version_text(handoff.candidate),
        "fallback_version": version_text(handoff.fallback),
        "removed_version": version_text(handoff.removed),
        "branch": handoff.branch,
        "pull_request_title": handoff.pull_request_title,
        "release_url": handoff.release_url,
        "provenance": {
            "tag": handoff.provenance.tag,
            "tag_object_sha": handoff.provenance.tag_object_sha,
            "target_commit_sha": handoff.provenance.target_commit_sha,
            "signature_verified": handoff.provenance.signature_verified,
            "verification_reason": handoff.provenance.verification_reason,
            "verified_at": handoff.provenance.verified_at,
        },
        "packaging_changes": handoff.packaging_changes,
    }
    (destination / "release.json").write_text(
        json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8"
    )
    for relative_path, source_path in source_files.items():
        shutil.copy2(source_path, destination / relative_path)
    return validate_handoff(destination)


def _require_destination_regular_file(path: Path, description: str) -> None:
    if not path.exists():
        raise HandoffError(f"{description} is missing from the fresh checkout.")
    _require_regular_file(path, description)


def apply_handoff(
    handoff_root: Path, repository_root: Path, *, expected_base_commit: str | None = None
) -> ReleaseHandoff:
    """Apply exactly the validated release files to a fresh checkout of main."""
    # Publish начинает с fresh checkout и считает скачанный artifact враждебным,
    # пока не проверит его schema, paths и provenance.
    handoff = validate_handoff(handoff_root)
    if expected_base_commit is not None and handoff.base_commit != _parse_commit(
        expected_base_commit, "expected_base_commit"
    ):
        raise HandoffError("Fresh checkout commit does not match the handoff base.")
    state = validate_overlay(repository_root)
    if state.fallback.version != handoff.removed or state.current.version != handoff.fallback:
        raise HandoffError("Fresh checkout no longer matches the handoff base rotation.")

    package_directory = repository_root / "gui-apps" / "noctalia"
    _require_directory(package_directory, "Fresh checkout package directory")
    readme_path = repository_root / "README.md"
    manifest_path = package_directory / "Manifest"
    removed_path = package_directory / f"noctalia-{version_text(handoff.removed)}.ebuild"
    candidate_path = package_directory / f"noctalia-{version_text(handoff.candidate)}.ebuild"
    _require_destination_regular_file(readme_path, "Fresh checkout README.md")
    _require_destination_regular_file(manifest_path, "Fresh checkout Manifest")
    _require_destination_regular_file(removed_path, "Fresh checkout removed ebuild")
    if candidate_path.exists() or candidate_path.is_symlink():
        raise HandoffError("Candidate ebuild already exists in the fresh checkout.")

    shutil.copy2(handoff_root / "README.md", readme_path)
    shutil.copy2(handoff_root / "gui-apps" / "noctalia" / "Manifest", manifest_path)
    shutil.copy2(handoff_root / version_path(handoff.candidate), candidate_path)
    removed_path.unlink()
    try:
        applied_state = validate_overlay(repository_root)
    except OverlayPolicyError as error:
        raise HandoffError("Applied handoff violates overlay policy.") from error
    if (
        applied_state.fallback.version != handoff.fallback
        or applied_state.current.version != handoff.candidate
    ):
        raise HandoffError("Applied handoff has an unexpected overlay state.")
    return handoff


def draft_pull_request_body(handoff: ReleaseHandoff) -> str:
    """Render the existing manual-review checklist from validated handoff fields."""
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
            "- OpenPGP tag signature verified by GitHub",
            "- `pkgcheck scan`",
            "",
            "## Upstream provenance",
            "",
            "- Annotated Git tag: verified",
            "- OpenPGP signature: verified by GitHub",
            f"- Tag object: `{handoff.provenance.tag_object_sha}`",
            f"- Target commit: `{handoff.provenance.target_commit_sha}`",
            f"- Verification reason: `{handoff.provenance.verification_reason}`",
            f"- Verified at: `{handoff.provenance.verified_at or 'not reported'}`",
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


def write_pull_request_request(path: Path, handoff: ReleaseHandoff) -> None:
    """Write the REST request only after validating every artifact field."""
    payload = {
        "base": "main",
        "body": draft_pull_request_body(handoff),
        "draft": True,
        "head": handoff.branch,
        "title": handoff.pull_request_title,
    }
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


def write_github_output(path: Path, handoff: ReleaseHandoff) -> None:
    """Write shell-safe, validated values for the publish workflow steps."""
    lines = (
        f"release_tag={handoff.release_tag}",
        f"base_commit={handoff.base_commit}",
        f"candidate_version={version_text(handoff.candidate)}",
        f"fallback_version={version_text(handoff.fallback)}",
        f"removed_version={version_text(handoff.removed)}",
        f"branch={handoff.branch}",
    )
    with path.open("a", encoding="utf-8") as output:
        output.write("\n".join(lines) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    copy_parser = subparsers.add_parser("copy-workspace")
    copy_parser.add_argument("--source", type=Path, required=True)
    copy_parser.add_argument("--destination", type=Path, required=True)

    eligibility_parser = subparsers.add_parser("publish-eligibility")
    eligibility_parser.add_argument("--outcome", required=True)
    eligibility_parser.add_argument("--dry-run", action="store_true")
    eligibility_parser.add_argument("--github-output", type=Path, required=True)

    snapshot_parser = subparsers.add_parser("snapshot-workspace")
    snapshot_parser.add_argument("--workspace", type=Path, required=True)
    snapshot_parser.add_argument("--output", type=Path, required=True)

    verify_parser = subparsers.add_parser("verify-workspace")
    verify_parser.add_argument("--workspace", type=Path, required=True)
    verify_parser.add_argument("--baseline", type=Path, required=True)

    create_parser = subparsers.add_parser("create")
    create_parser.add_argument("--source-root", type=Path, required=True)
    create_parser.add_argument("--destination", type=Path, required=True)
    create_parser.add_argument("--watch-result", type=Path, required=True)
    create_parser.add_argument("--release-tag", required=True)
    create_parser.add_argument("--base-commit", required=True)
    create_parser.add_argument("--removed-version", required=True)
    create_parser.add_argument("--fallback-version", required=True)
    create_parser.add_argument("--candidate-version", required=True)

    apply_parser = subparsers.add_parser("apply")
    apply_parser.add_argument("--handoff-root", type=Path, required=True)
    apply_parser.add_argument("--repository-root", type=Path, required=True)
    apply_parser.add_argument("--expected-base-commit", required=True)
    apply_parser.add_argument("--pull-request-request", type=Path, required=True)
    apply_parser.add_argument("--github-output", type=Path, required=True)

    args = parser.parse_args()
    try:
        if args.command == "copy-workspace":
            copy_sanitized_workspace(args.source, args.destination)
        elif args.command == "publish-eligibility":
            ready = publish_is_required(args.outcome, args.dry_run)
            args.github_output.write_text(
                f"publish_ready={'true' if ready else 'false'}\n", encoding="utf-8"
            )
        elif args.command == "snapshot-workspace":
            write_workspace_integrity(args.workspace, args.output)
        elif args.command == "verify-workspace":
            verify_workspace_integrity(args.workspace, args.baseline)
        elif args.command == "create":
            create_handoff(
                args.source_root,
                args.destination,
                args.watch_result,
                release_tag=args.release_tag,
                base_commit=args.base_commit,
                removed=_parse_version(args.removed_version, "removed_version"),
                fallback=_parse_version(args.fallback_version, "fallback_version"),
                candidate=_parse_version(args.candidate_version, "candidate_version"),
            )
        else:
            handoff = apply_handoff(
                args.handoff_root,
                args.repository_root,
                expected_base_commit=args.expected_base_commit,
            )
            write_pull_request_request(args.pull_request_request, handoff)
            write_github_output(args.github_output, handoff)
        return 0
    except (HandoffError, OverlayPolicyError) as error:
        print(f"Release handoff error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
