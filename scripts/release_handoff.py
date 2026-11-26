#!/usr/bin/env python3
"""Create and validate the minimal handoff from release preparation to publishing."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import zipfile
import zlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

SCRIPT_DIRECTORY = str(Path(__file__).parent)
if SCRIPT_DIRECTORY not in sys.path:
    sys.path.insert(0, SCRIPT_DIRECTORY)

from overlay_policy import (  # noqa: E402
    OverlayPolicyError,
    VERSION_BLOCK_PATTERN,
    Version,
    parse_version,
    rendered_version_block,
    validate_overlay,
    version_text,
)


HANDOFF_SCHEMA_VERSION = 2
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

SOURCE_URI = 'SRC_URI="https://github.com/noctalia-dev/noctalia/archive/v${PV}.tar.gz -> ${P}.tar.gz"'
MANDATORY_GATES = (
    "checkout", "discovery", "unit_tests", "rotation", "overlay_policy", "source",
    "manifest_generation", "pkgcheck", "manifest", "handoff", "upload",
)


class HandoffError(OverlayPolicyError):
    """Raised when a release handoff violates its trust boundary."""


@dataclass(frozen=True)
class UpstreamProvenance:
    """Validated tag resolution and optional GitHub signature metadata."""

    tag: str
    tag_type: str
    tag_object_sha: str | None
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
            "README.en.md",
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


def parse_commit(value: object, field: str) -> str:
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
        "tag_type",
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
    tag_type = value["tag_type"]
    signature_verified = value["signature_verified"]
    reason = value["verification_reason"]
    verified_at = _parse_verified_at(value["verified_at"])
    if type(signature_verified) is not bool:
        raise HandoffError("Handoff provenance signature_verified must be boolean.")
    if not isinstance(reason, str) or not reason:
        raise HandoffError("Handoff provenance verification reason is invalid.")
    target_commit_sha = parse_commit(
        value["target_commit_sha"], "target_commit_sha"
    )
    if tag_type == "lightweight":
        if (
            value["tag_object_sha"] is not None
            or signature_verified is not False
            or reason != "not-applicable"
            or verified_at is not None
        ):
            raise HandoffError("Handoff lightweight provenance is inconsistent.")
        tag_object_sha = None
    elif tag_type == "annotated":
        tag_object_sha = parse_commit(value["tag_object_sha"], "tag_object_sha")
        if signature_verified and reason != "valid":
            raise HandoffError("Verified handoff provenance must have reason valid.")
        if not signature_verified and reason in {"valid", "not-applicable"}:
            raise HandoffError("Unverified annotated provenance has a contradictory reason.")
    else:
        raise HandoffError("Handoff provenance tag_type is invalid.")
    return UpstreamProvenance(
        tag=release_tag,
        tag_type=tag_type,
        tag_object_sha=tag_object_sha,
        target_commit_sha=target_commit_sha,
        signature_verified=signature_verified,
        verification_reason=reason,
        verified_at=verified_at,
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
        base_commit=parse_commit(value["base_commit"], "base_commit"),
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
        base_commit=parse_commit(base_commit, "base_commit"),
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
        "README.en.md": source_root / "README.en.md",
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
            "tag_type": handoff.provenance.tag_type,
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
    if expected_base_commit is not None and handoff.base_commit != parse_commit(
        expected_base_commit, "expected_base_commit"
    ):
        raise HandoffError("Fresh checkout commit does not match the handoff base.")
    state = validate_overlay(repository_root)
    if state.fallback.version != handoff.removed or state.current.version != handoff.fallback:
        raise HandoffError("Fresh checkout no longer matches the handoff base rotation.")

    package_directory = repository_root / "gui-apps" / "noctalia"
    _require_directory(package_directory, "Fresh checkout package directory")
    readme_path = repository_root / "README.md"
    english_readme_path = repository_root / "README.en.md"
    manifest_path = package_directory / "Manifest"
    removed_path = package_directory / f"noctalia-{version_text(handoff.removed)}.ebuild"
    candidate_path = package_directory / f"noctalia-{version_text(handoff.candidate)}.ebuild"
    _require_destination_regular_file(readme_path, "Fresh checkout README.md")
    _require_destination_regular_file(english_readme_path, "Fresh checkout README.en.md")
    _require_destination_regular_file(manifest_path, "Fresh checkout Manifest")
    _require_destination_regular_file(removed_path, "Fresh checkout removed ebuild")
    if candidate_path.exists() or candidate_path.is_symlink():
        raise HandoffError("Candidate ebuild already exists in the fresh checkout.")

    shutil.copy2(handoff_root / "README.md", readme_path)
    shutil.copy2(handoff_root / "README.en.md", english_readme_path)
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


def distfile_name(version: Version) -> str:
    return f"noctalia-{version_text(version)}.tar.gz"


def source_url(tag: str) -> str:
    return f"https://github.com/noctalia-dev/noctalia/archive/{tag}.tar.gz"


def archive_integrity(path: Path) -> dict[str, int | str]:
    hashes = {"blake2b": hashlib.blake2b(), "sha512": hashlib.sha512()}
    size = 0
    with path.open("rb") as archive:
        for block in iter(lambda: archive.read(1024 * 1024), b""):
            size += len(block)
            for digest in hashes.values():
                digest.update(block)
    return {"size": size, **{name: digest.hexdigest() for name, digest in hashes.items()}}


def verify_git_archive(path: Path, commit: str) -> None:
    """Read the whole gzip and tar before accepting Git's early commit-ID result."""
    try:
        with tempfile.TemporaryFile() as tar, gzip.open(path, "rb") as compressed:
            shutil.copyfileobj(compressed, tar)
            tar.seek(0)
            with tarfile.open(fileobj=tar, mode="r:") as archive:
                initial_comment = archive.pax_headers.get("comment")
                for _member in archive:
                    pass
                final_comment = archive.pax_headers.get("comment")
                tar.seek(archive.offset)
                # tarfile can stop at an invalid header or EOF. Require the tar
                # terminator and zero-only padding beyond the last parsed member.
                if tar.read(1024) != bytes(1024) or any(
                    any(block) for block in iter(lambda: tar.read(65536), b"")
                ):
                    raise HandoffError("Source tar is incomplete or has invalid trailing data.")
            tar.seek(0)
            # Git intentionally reads only a prefix. A seekable file avoids SIGPIPE.
            result = subprocess.run(
                ["git", "get-tar-commit-id"], stdin=tar, capture_output=True, check=False
            )
    except (EOFError, OSError, tarfile.TarError, zlib.error) as error:
        raise HandoffError("Source gzip/tar integrity check failed.") from error
    if (
        result.returncode or result.stdout != (commit + "\n").encode("ascii")
        or initial_comment != commit or final_comment != commit
    ):
        raise HandoffError("Source tar commit ID is missing, malformed or different from discovery.")


def verify_source(
    source_root: Path, watch_result: Path, tag: str, distdir: Path, summary: Path
) -> dict:
    candidate = _parse_version(tag.removeprefix("v"), "candidate_version")
    if tag != f"v{version_text(candidate)}":
        raise HandoffError("Source tag does not match a stable candidate.")
    _, provenance, _ = _watch_result_details(watch_result, tag)
    ebuild = source_root / version_path(candidate)
    if SOURCE_URI not in ebuild.read_text(encoding="utf-8").splitlines():
        raise HandoffError(
            f"{ebuild}: expected the exact source declaration {SOURCE_URI}. "
            "Portage must use the same archive URL and distfile name as canonical archive verification."
        )
    url = source_url(tag)
    evidence = {
        "candidate": version_text(candidate),
        "tag": tag,
        "commit": provenance.target_commit_sha,
        "url": url,
    }
    checks = dict.fromkeys(("download", "gzip_tar_and_commit", "byte_integrity"), "not run")
    append_summary(summary, f"Source candidate {tag} at {provenance.target_commit_sha}: {url}")
    gate = "download"
    try:
        distdir.mkdir()
        path = distdir / distfile_name(candidate)
        download = subprocess.run(
            [
                "curl", "--disable", "--fail", "--silent", "--show-error", "--location",
                "--max-redirs", "3", "--proto", "=https", "--proto-redir", "=https",
                "--max-time", "180", "--output", str(path), "--write-out",
                "%{url_effective}", url,
            ],
            capture_output=True, text=True, check=False,
        )
        if download.returncode or download.stdout not in {
            url, f"https://codeload.github.com/noctalia-dev/noctalia/tar.gz/refs/tags/{tag}",
            f"https://codeload.github.com/noctalia-dev/noctalia/tar.gz/{tag}",
        }:
            raise HandoffError("Canonical GitHub archive download failed.")
        checks[gate] = "passed"
        gate = "gzip_tar_and_commit"
        verify_git_archive(path, provenance.target_commit_sha)
        checks[gate] = "passed"
        gate = "byte_integrity"
        evidence["archive"] = archive_integrity(path)
        checks[gate] = "passed"
        append_summary(summary, f"Source archive integrity: `{json.dumps(evidence['archive'])}`")
        return evidence
    except (HandoffError, OSError):
        checks[gate] = "failed"
        raise
    finally:
        for name, status in checks.items():
            append_summary(summary, f"- {name}: {status}")


def manifest_entries(path: Path) -> dict[str, str]:
    entries = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        fields = line.split()
        if (
            len(fields) != 7
            or fields[0] != "DIST"
            or fields[1] in entries
            or re.fullmatch(r"[1-9][0-9]*", fields[2]) is None
            or fields[3] != "BLAKE2B"
            or fields[5] != "SHA512"
            or any(re.fullmatch(r"[0-9a-f]{128}", fields[i]) is None for i in (4, 6))
        ):
            raise HandoffError("Unexpected or duplicate Manifest DIST entry.")
        entries[fields[1]] = line
    return entries


def verify_manifest(
    manifest: Path, base_manifest: Path, candidate: Version, fallback: Version,
    integrity: dict, summary: Path | None = None,
) -> None:
    name = distfile_name(candidate)
    fallback_name = distfile_name(fallback)
    checks = dict.fromkeys(("Manifest entry set", "DIST size", "BLAKE2B", "SHA512", "Fallback DIST"), "not run")
    gate = "Manifest entry set"
    try:
        entries = manifest_entries(manifest)
        if set(entries) != {name, fallback_name}:
            raise HandoffError("Manifest must contain exactly candidate and retained fallback DIST entries.")
        checks[gate] = "passed"
        fields = entries[name].split()
        for gate, actual, expected in (
            ("DIST size", fields[2], str(integrity["size"])),
            ("BLAKE2B", fields[4], integrity["blake2b"]),
            ("SHA512", fields[6], integrity["sha512"]),
        ):
            if actual != expected:
                raise HandoffError(f"{gate} differs from the verified archive or accepted base.")
            checks[gate] = "passed"
        gate = "Fallback DIST"
        if entries[fallback_name] != manifest_entries(base_manifest).get(fallback_name):
            raise HandoffError("Fallback DIST differs from the accepted base.")
        checks[gate] = "passed"
    except (HandoffError, OSError):
        checks[gate] = "failed"
        raise
    finally:
        if summary is not None:
            for name, status in checks.items():
                append_summary(summary, f"- {name}: {status}")


def append_summary(path: Path, line: str) -> None:
    with path.open("a", encoding="utf-8") as summary:
        print(line, file=summary)


def _positive_id(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[1-9][0-9]*", value) is not None


def _hex_digest(value: object, length: int) -> bool:
    return isinstance(value, str) and re.fullmatch(rf"[0-9a-f]{{{length}}}", value) is not None


def check_candidate_evidence(evidence: object, handoff: ReleaseHandoff | None = None) -> None:
    if not isinstance(evidence, dict) or set(evidence) != {"candidate", "tag", "commit", "url", "archive"}:
        raise HandoffError("Candidate source evidence is incomplete.")
    candidate = _parse_version(evidence["candidate"], "candidate")
    tag = f"v{version_text(candidate)}"
    commit = parse_commit(evidence["commit"], "source commit")
    archive = evidence["archive"]
    if (
        evidence["tag"] != tag
        or evidence["url"] != source_url(tag)
        or not isinstance(archive, dict)
        or set(archive) != {"size", "blake2b", "sha512"}
        or type(archive["size"]) is not int
        or archive["size"] <= 0
        or not all(_hex_digest(archive[key], 128) for key in ("blake2b", "sha512"))
    ):
        raise HandoffError("Candidate source evidence is malformed or inconsistent.")
    if handoff and (
        evidence["candidate"] != version_text(handoff.candidate)
        or tag != handoff.release_tag
        or commit != handoff.provenance.target_commit_sha
    ):
        raise HandoffError("Candidate source evidence differs from the handoff.")


def record_readiness(handoff_root: Path, evidence_path: Path, env: Mapping[str, str]) -> dict:
    handoff = validate_handoff(handoff_root)
    evidence = _load_json(evidence_path, "source evidence")
    check_candidate_evidence(evidence, handoff)
    readiness = {
        "run": env.get("GITHUB_RUN_ID"),
        "attempt": env.get("GITHUB_RUN_ATTEMPT"),
        "artifact": env.get("UPLOADED_ARTIFACT_ID"),
        "digest": env.get("UPLOADED_ARTIFACT_DIGEST"),
        "base": handoff.base_commit,
        "source": evidence,
        "gates": dict.fromkeys(MANDATORY_GATES, "passed"),
    }
    _check_readiness(readiness, env)
    return readiness


def _check_readiness(ready: object, env: Mapping[str, str]) -> None:
    if not isinstance(ready, dict) or set(ready) != {
        "run", "attempt", "artifact", "digest", "base", "source", "gates",
    }:
        raise HandoffError("Release readiness is missing or incomplete.")
    if not all(_positive_id(ready[field]) for field in ("run", "attempt", "artifact")):
        raise HandoffError("Release readiness contains an invalid run, attempt or artifact ID.")
    if ready["run"] != env.get("GITHUB_RUN_ID") or ready["attempt"] != env.get("GITHUB_RUN_ATTEMPT"):
        raise HandoffError("Prepare belongs to another run or attempt. Run the entire workflow again.")
    if not _hex_digest(ready["digest"], 64) or ready["gates"] != dict.fromkeys(MANDATORY_GATES, "passed"):
        raise HandoffError("Artifact digest or mandatory prepare evidence is missing.")
    if parse_commit(ready["base"], "readiness base") != env.get("GITHUB_SHA"):
        raise HandoffError("Prepare readiness base differs from the trusted run SHA.")
    check_candidate_evidence(ready["source"])


def load_prepare_readiness(env: Mapping[str, str]) -> dict:
    """Read the Prepare output and verify that it belongs to this run and attempt."""
    try:
        ready = json.loads(env.get("PREPARE_READY", ""), object_pairs_hook=_reject_duplicate_json_keys)
    except ValueError as error:
        raise HandoffError("Prepare readiness is missing or invalid.") from error
    _check_readiness(ready, env)
    return ready


def git(root: Path, *args: str, env: dict[str, str] | None = None) -> bytes:
    result = subprocess.run(["git", "-C", str(root), *args], env=env, capture_output=True, check=False)
    if result.returncode:
        raise HandoffError(f"Git {args[0]} failed (exit {result.returncode}).")
    return result.stdout


def verify_checkout(root: Path, sha: str) -> None:
    parse_commit(sha, "trusted run SHA")
    if git(root, "rev-parse", "HEAD") != (sha + "\n").encode("ascii"):
        raise HandoffError("Checkout HEAD differs from the trusted run SHA.")
    if git(root, "status", "--porcelain", "--untracked-files=all"):
        raise HandoffError("Publication requires a clean checkout and index.")


def unpack_verified_artifact(download: Path, destination: Path, ready: dict) -> ReleaseHandoff:
    _require_directory(download, "Artifact download directory")
    archives = list(download.iterdir())
    if len(archives) != 1:
        raise HandoffError("Expected one archived handoff from the exact upload ID.")
    archive_path = archives[0]
    metadata = _lstat(archive_path, "Downloaded handoff ZIP")
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 6 * MAX_HANDOFF_FILE_SIZE:
        raise HandoffError("Invalid downloaded handoff ZIP.")
    archive_bytes = archive_path.read_bytes()
    if hashlib.sha256(archive_bytes).hexdigest() != ready["digest"]:
        raise HandoffError("Downloaded artifact SHA256 differs from the successful upload.")
    candidate = _parse_version(ready["source"]["candidate"], "candidate")
    expected = handoff_file_paths(candidate)
    directories = {"gui-apps", "gui-apps/noctalia"}
    seen = set()
    try:
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            destination.mkdir()
            for directory in sorted(directories):
                (destination / directory).mkdir()
            for entry in archive.infolist():
                name = entry.filename
                mode = stat.S_IFMT(entry.external_attr >> 16)
                if entry.orig_filename != name or name in seen:
                    raise HandoffError("Artifact ZIP has duplicate or malformed paths.")
                seen.add(name)
                if entry.is_dir() and name[:-1] in directories and mode in (0, stat.S_IFDIR):
                    continue
                if (
                    name not in expected
                    or mode not in (0, stat.S_IFREG)
                    or entry.file_size > MAX_HANDOFF_FILE_SIZE
                ):
                    raise HandoffError("Artifact ZIP has an unexpected path, type or file size.")
                (destination / name).write_bytes(archive.read(entry))
    except HandoffError:
        raise
    except (zipfile.BadZipFile, RuntimeError, zlib.error) as error:
        raise HandoffError("Unable to extract the verified release handoff.") from error

    handoff = validate_handoff(destination)
    if handoff.base_commit != ready["base"]:
        raise HandoffError("Handoff base differs from the trusted run SHA.")
    check_candidate_evidence(ready["source"], handoff)
    return handoff


def validate_and_apply_candidate(
    root: Path, destination: Path, handoff: ReleaseHandoff, ready: dict,
) -> None:
    """Verify the candidate files and apply them to the trusted checkout."""
    verify_manifest(
        destination / MANIFEST_PATH, root / MANIFEST_PATH, handoff.candidate,
        handoff.fallback, ready["source"]["archive"],
    )

    readmes = {name: (root / name).read_text(encoding="utf-8") for name in ("README.md", "README.en.md")}
    if (destination / version_path(handoff.candidate)).read_bytes() != (
        root / version_path(handoff.fallback)
    ).read_bytes():
        raise HandoffError("Candidate ebuild differs from the retained ebuild template.")
    apply_handoff(destination, root, expected_base_commit=ready["base"])
    state = validate_overlay(root)
    for name, original in readmes.items():
        expected, count = VERSION_BLOCK_PATTERN.subn(rendered_version_block(state, name), original)
        if count != 1 or (root / name).read_bytes() != expected.encode("utf-8"):
            raise HandoffError(f"{name} differs from the expected generated README.")


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

    create_parser = subparsers.add_parser("create")
    create_parser.add_argument("--source-root", type=Path, required=True)
    create_parser.add_argument("--destination", type=Path, required=True)
    create_parser.add_argument("--watch-result", type=Path, required=True)
    create_parser.add_argument("--release-tag", required=True)
    create_parser.add_argument("--base-commit", required=True)
    create_parser.add_argument("--removed-version", required=True)
    create_parser.add_argument("--fallback-version", required=True)
    create_parser.add_argument("--candidate-version", required=True)

    checkout_parser = subparsers.add_parser("verify-checkout")
    checkout_parser.add_argument("--repository-root", type=Path, required=True)

    source_parser = subparsers.add_parser("verify-source")
    source_parser.add_argument("--source-root", type=Path, required=True)
    source_parser.add_argument("--watch-result", type=Path, required=True)
    source_parser.add_argument("--release-tag", required=True)
    source_parser.add_argument("--distdir", type=Path, required=True)
    source_parser.add_argument("--evidence", type=Path, required=True)

    manifest_parser = subparsers.add_parser("verify-manifest")
    manifest_parser.add_argument("--source-root", type=Path, required=True)
    manifest_parser.add_argument("--repository-root", type=Path, required=True)
    manifest_parser.add_argument("--fallback-version", required=True)
    manifest_parser.add_argument("--distdir", type=Path, required=True)
    manifest_parser.add_argument("--evidence", type=Path, required=True)

    ready_parser = subparsers.add_parser("ready")
    ready_parser.add_argument("--handoff-root", type=Path, required=True)
    ready_parser.add_argument("--evidence", type=Path, required=True)

    args = parser.parse_args()
    try:
        if args.command == "copy-workspace":
            copy_sanitized_workspace(args.source, args.destination)
        elif args.command == "publish-eligibility":
            ready = publish_is_required(args.outcome, args.dry_run)
            args.github_output.write_text(
                f"publish_ready={'true' if ready else 'false'}\n", encoding="utf-8"
            )
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
        elif args.command == "verify-checkout":
            verify_checkout(args.repository_root, os.environ["GITHUB_SHA"])
        elif args.command == "verify-source":
            evidence = verify_source(
                args.source_root, args.watch_result, args.release_tag, args.distdir,
                Path(os.environ["GITHUB_STEP_SUMMARY"]),
            )
            args.evidence.write_text(json.dumps(evidence), encoding="utf-8")
        elif args.command == "verify-manifest":
            evidence = _load_json(args.evidence, "source evidence")
            check_candidate_evidence(evidence)
            candidate = _parse_version(evidence["candidate"], "candidate")
            summary = Path(os.environ["GITHUB_STEP_SUMMARY"])
            if archive_integrity(args.distdir / distfile_name(candidate)) != evidence["archive"]:
                append_summary(summary, "- DISTDIR verified bytes after Portage: failed")
                raise HandoffError("Candidate distfile changed during Manifest generation.")
            append_summary(summary, "- DISTDIR verified bytes after Portage: passed")
            verify_manifest(
                args.source_root / MANIFEST_PATH, args.repository_root / MANIFEST_PATH,
                candidate, _parse_version(args.fallback_version, "fallback"),
                evidence["archive"], summary,
            )
        elif args.command == "ready":
            readiness = record_readiness(args.handoff_root, args.evidence, os.environ)
            append_summary(
                Path(os.environ["GITHUB_STEP_SUMMARY"]),
                f"\n## Release readiness\n\n```json\n{json.dumps(readiness, indent=2)}\n```\n",
            )
            with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
                print(f"readiness={json.dumps(readiness, separators=(',', ':'))}", file=output)
        return 0
    except (OverlayPolicyError, OSError, UnicodeError) as error:
        print(f"Release handoff error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
