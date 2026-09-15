#!/usr/bin/env python3
"""Find stable Noctalia releases that need a draft overlay pull request."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, urlopen

SCRIPT_DIRECTORY = str(Path(__file__).parent)
if SCRIPT_DIRECTORY not in sys.path:
    sys.path.insert(0, SCRIPT_DIRECTORY)

from overlay_policy import (  # noqa: E402
    OverlayPolicyError,
    Version,
    validate_overlay,
    version_text,
)


API_VERSION = "2022-11-28"
UPSTREAM_REPOSITORY = "noctalia-dev/noctalia"
LATEST_RELEASE_URL = (
    f"https://api.github.com/repos/{UPSTREAM_REPOSITORY}/releases/latest"
)
SEARCH_API_URL = "https://api.github.com/search/issues"
PULLS_API_URL = "https://api.github.com/repos/{repository}/pulls"
CONTENTS_API_URL = "https://api.github.com/repos/{repository}/contents/{path}"
TAG_PATTERN = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
PACKAGING_FILES = ("PACKAGING.md", "meson.build", "meson_options.txt")


class WatcherError(RuntimeError):
    """Raised for invalid input or a GitHub API failure."""


@dataclass(frozen=True)
class Release:
    tag: str
    version: Version
    url: str


@dataclass(frozen=True)
class PackagingChanges:
    packaging_md: str
    meson_build: str
    meson_options: str


@dataclass(frozen=True)
class WatchResult:
    outcome: str
    packaged_version: Version
    release: Release
    packaging_changes: PackagingChanges | None = None


class ReleaseClient(Protocol):
    def latest_release(self) -> Release: ...

    def pull_request_exists(self, title: str, branch: str) -> bool: ...

    def upstream_file_sha(self, path: str, tag: str) -> str | None: ...


class GitHubClient:
    """Small GitHub REST client for release discovery only."""

    def __init__(self, token: str, repository: str) -> None:
        self._token = token
        self._repository = repository

    def latest_release(self) -> Release:
        return parse_stable_release(self._request_json(LATEST_RELEASE_URL))

    def pull_request_exists(self, title: str, branch: str) -> bool:
        query = f'repo:{self._repository} is:pr is:open in:title "{title}"'
        payload = self._request_json(
            f"{SEARCH_API_URL}?{urlencode({'q': query, 'per_page': 1})}"
        )
        total_count = payload.get("total_count")
        if not isinstance(total_count, int):
            raise WatcherError(
                "GitHub API returned an invalid pull request search response."
            )
        if total_count > 0:
            return True
        owner = self._repository.split("/", maxsplit=1)[0]
        pull_requests = self._request_list(
            PULLS_API_URL.format(repository=self._repository),
            query={"state": "open", "head": f"{owner}:{branch}", "per_page": "1"},
        )
        return bool(pull_requests)

    def upstream_file_sha(self, path: str, tag: str) -> str | None:
        payload = self._request_json(
            CONTENTS_API_URL.format(repository=UPSTREAM_REPOSITORY, path=quote(path)),
            query={"ref": tag},
            allow_not_found=True,
        )
        if payload is None:
            return None
        sha = payload.get("sha")
        if not isinstance(sha, str):
            raise WatcherError(
                f"GitHub API returned an invalid response for {path}."
            )
        return sha

    def _request_json(
        self,
        url: str,
        *,
        query: dict[str, str] | None = None,
        allow_not_found: bool = False,
    ) -> dict[str, Any] | None:
        payload = self._request_payload(
            url, query=query, allow_not_found=allow_not_found
        )
        if payload is None:
            return None
        if not isinstance(payload, dict):
            raise WatcherError("GitHub API returned an invalid JSON object.")
        return payload

    def _request_list(self, url: str, *, query: dict[str, str]) -> list[Any]:
        payload = self._request_payload(url, query=query)
        if not isinstance(payload, list):
            raise WatcherError("GitHub API returned an invalid JSON array.")
        return payload

    def _request_payload(
        self,
        url: str,
        *,
        query: dict[str, str] | None = None,
        allow_not_found: bool = False,
    ) -> Any | None:
        if query is not None:
            url = f"{url}?{urlencode(query)}"
        request = Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self._token}",
                "User-Agent": "noctalia-overlay-release-watcher",
                "X-GitHub-Api-Version": API_VERSION,
            },
        )
        try:
            with urlopen(request, timeout=20) as response:  # noqa: S310
                payload = json.load(response)
        except HTTPError as error:
            if allow_not_found and error.code == 404:
                return None
            raise WatcherError(
                f"GitHub API request failed with HTTP {error.code}."
            ) from error
        except URLError as error:
            raise WatcherError(f"GitHub API request failed: {error.reason}.") from error
        return payload


def parse_stable_release(payload: dict[str, Any]) -> Release:
    """Validate a latest-release API response against the stable-tag policy."""
    if payload.get("draft") is not False or payload.get("prerelease") is not False:
        raise WatcherError("The latest upstream release is not a stable release.")

    tag = payload.get("tag_name")
    release_url = payload.get("html_url")
    if not isinstance(tag, str) or not isinstance(release_url, str):
        raise WatcherError("GitHub API returned an invalid release response.")
    match = TAG_PATTERN.fullmatch(tag)
    if match is None:
        raise WatcherError(f"The latest upstream tag is not stable semver: {tag}.")

    parsed_url = urlparse(release_url)
    if parsed_url.scheme != "https" or parsed_url.netloc != "github.com":
        raise WatcherError("The latest upstream release has an unexpected URL.")
    return Release(tag=tag, version=tuple(map(int, match.groups())), url=release_url)


def draft_pull_request_title(release: Release) -> str:
    """Return the deterministic title used for idempotent PR lookup."""
    return f"[release-bump] Noctalia {release.tag}"


def draft_pull_request_branch(release: Release) -> str:
    """Return the deterministic branch used to resume a previously failed run."""
    return f"automation/noctalia-{release.tag}"


def packaging_changes(
    client: ReleaseClient, previous_tag: str, release_tag: str
) -> PackagingChanges:
    """Compare exact upstream packaging files by immutable Git blob SHA."""
    states = {}
    for path in PACKAGING_FILES:
        before = client.upstream_file_sha(path, previous_tag)
        after = client.upstream_file_sha(path, release_tag)
        if after is None:
            states[path] = "not present"
        elif before != after:
            states[path] = "modified"
        else:
            states[path] = "unchanged"
    return PackagingChanges(
        packaging_md=states["PACKAGING.md"],
        meson_build=states["meson.build"],
        meson_options=states["meson_options.txt"],
    )


def watch(
    client: ReleaseClient, repository_root: Path, *, dry_run: bool
) -> WatchResult:
    """Discover one unreported release without making any repository changes."""
    state = validate_overlay(repository_root)
    release = client.latest_release()
    if state.current.version >= release.version:
        return WatchResult("up-to-date", state.current.version, release)
    if client.pull_request_exists(
        draft_pull_request_title(release), draft_pull_request_branch(release)
    ):
        return WatchResult("already-reported", state.current.version, release)

    changes = packaging_changes(
        client,
        f"v{version_text(state.current.version)}",
        release.tag,
    )
    outcome = "dry-run" if dry_run else "release-available"
    return WatchResult(outcome, state.current.version, release, changes)


def result_payload(result: WatchResult) -> dict[str, Any]:
    """Return a machine-readable result suitable for the preparation step."""
    payload: dict[str, Any] = {
        "outcome": result.outcome,
        "packaged_version": version_text(result.packaged_version),
        "release": asdict(result.release),
    }
    if result.packaging_changes is not None:
        payload["packaging_changes"] = asdict(result.packaging_changes)
    return payload


def write_summary(result: WatchResult, summary_path: Path | None) -> None:
    """Append an informative, non-sensitive result to GitHub Step Summary."""
    if summary_path is None:
        return
    lines = [
        "## Noctalia release watcher",
        "",
        f"- Packaged stable version: `{version_text(result.packaged_version)}`",
        f"- Latest upstream release: [`{result.release.tag}`]({result.release.url})",
        f"- Result: `{result.outcome}`",
    ]
    if result.packaging_changes is not None:
        lines.extend(
            (
                "",
                "Upstream packaging changes:",
                "",
                f"- `PACKAGING.md`: {result.packaging_changes.packaging_md}",
                f"- `meson.build`: {result.packaging_changes.meson_build}",
                f"- `meson_options.txt`: {result.packaging_changes.meson_options}",
            )
        )
    with summary_path.open("a", encoding="utf-8") as summary:
        summary.write("\n".join(lines) + "\n")


def write_github_output(path: Path, result: WatchResult) -> None:
    """Write values constrained by validation to the workflow output file."""
    has_update = result.outcome in {"release-available", "dry-run"}
    lines = (
        f"has_update={'true' if has_update else 'false'}",
        f"outcome={result.outcome}",
        f"release_tag={result.release.tag}",
    )
    with path.open("a", encoding="utf-8") as output:
        output.write("\n".join(lines) + "\n")


def required_environment(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise WatcherError(f"{name} is not set.")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--result-json", type=Path, required=True)
    parser.add_argument("--github-output", type=Path)
    parser.add_argument("--summary", type=Path)
    args = parser.parse_args()

    try:
        token = required_environment("GH_TOKEN")
        repository = required_environment("GITHUB_REPOSITORY")
        if REPOSITORY_PATTERN.fullmatch(repository) is None:
            raise WatcherError("GITHUB_REPOSITORY has an invalid format.")
        result = watch(
            GitHubClient(token, repository),
            args.repository_root,
            dry_run=args.dry_run,
        )
        args.result_json.write_text(
            json.dumps(result_payload(result), sort_keys=True) + "\n", encoding="utf-8"
        )
        write_summary(result, args.summary)
        if args.github_output is not None:
            write_github_output(args.github_output, result)
        print(f"Watcher result: {result.outcome}.")
        return 0
    except (OverlayPolicyError, WatcherError) as error:
        print(f"Watcher error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
