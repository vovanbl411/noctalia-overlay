#!/usr/bin/env python3
"""Create one GitHub Issue when Noctalia has a newer stable release."""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen


API_VERSION = "2022-11-28"
LATEST_RELEASE_URL = "https://api.github.com/repos/noctalia-dev/noctalia/releases/latest"
ISSUES_API_URL = "https://api.github.com/repos/{repository}/issues"
SEARCH_API_URL = "https://api.github.com/search/issues"
TAG_PATTERN = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
EBUILD_PATTERN = re.compile(r"^noctalia-(\d+)\.(\d+)\.(\d+)\.ebuild$")
REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")

Version = tuple[int, int, int]


class WatcherError(RuntimeError):
    """Raised for an invalid input or a GitHub API failure."""


@dataclass(frozen=True)
class Release:
    tag: str
    version: Version
    url: str


@dataclass(frozen=True)
class WatchResult:
    outcome: str
    packaged_version: Version
    release: Release
    issue_url: str | None = None


class ReleaseClient(Protocol):
    def latest_release(self) -> Release: ...

    def issue_exists(self, title: str) -> bool: ...

    def create_issue(self, title: str, body: str) -> str: ...


class GitHubClient:
    def __init__(self, token: str, repository: str) -> None:
        self._token = token
        self._repository = repository

    def latest_release(self) -> Release:
        return parse_stable_release(self._request_json(LATEST_RELEASE_URL))

    def issue_exists(self, title: str) -> bool:
        query = f'repo:{self._repository} is:issue in:title "{title}"'
        payload = self._request_json(
            f"{SEARCH_API_URL}?{urlencode({'q': query, 'per_page': 1})}"
        )
        total_count = payload.get("total_count")
        if not isinstance(total_count, int):
            raise WatcherError("GitHub API returned an invalid issue search response.")
        return total_count > 0

    def create_issue(self, title: str, body: str) -> str:
        payload = self._request_json(
            ISSUES_API_URL.format(repository=self._repository),
            method="POST",
            body={"title": title, "body": body},
        )
        issue_url = payload.get("html_url")
        if not isinstance(issue_url, str):
            raise WatcherError("GitHub API returned an invalid Issue response.")
        return issue_url

    def _request_json(
        self, url: str, *, method: str = "GET", body: dict[str, str] | None = None
    ) -> dict[str, Any]:
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = Request(
            url,
            data=data,
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
                "User-Agent": "noctalia-overlay-release-watcher",
                "X-GitHub-Api-Version": API_VERSION,
            },
        )

        try:
            with urlopen(request, timeout=20) as response:  # noqa: S310
                payload = json.load(response)
        except HTTPError as error:
            raise WatcherError(f"GitHub API request failed with HTTP {error.code}.") from error
        except URLError as error:
            raise WatcherError(f"GitHub API request failed: {error.reason}.") from error

        if not isinstance(payload, dict):
            raise WatcherError("GitHub API returned an invalid JSON object.")
        return payload


def parse_stable_release(payload: dict[str, Any]) -> Release:
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


def latest_packaged_version(repository_root: Path) -> Version:
    package_dir = repository_root / "gui-apps" / "noctalia"
    versions = [
        tuple(map(int, match.groups()))
        for ebuild in package_dir.glob("noctalia-*.ebuild")
        if (match := EBUILD_PATTERN.fullmatch(ebuild.name)) is not None
    ]
    if not versions:
        raise WatcherError("No stable Noctalia ebuild was found.")
    return max(versions)


def version_text(version: Version) -> str:
    return ".".join(map(str, version))


def issue_title(release: Release) -> str:
    return f"[upstream-release] Noctalia {release.tag}"


def issue_body(release: Release, packaged_version: Version) -> str:
    return "\n".join(
        (
            f"Upstream published stable release [{release.tag}]({release.url}).",
            "",
            "The highest stable version packaged in this overlay is "
            f"`{version_text(packaged_version)}`.",
            "",
            "Maintenance checklist:",
            "",
            "- [ ] Verify the upstream tag signature and review the release notes.",
            "- [ ] Review `PACKAGING.md` and dependency changes.",
            "- [ ] Add the ebuild and generate its Manifest from the release archive.",
            "- [ ] Run `pkgcheck scan` and `emerge -pv`.",
            "- [ ] Test the update manually before closing this Issue.",
        )
    )


def watch(
    client: ReleaseClient,
    repository_root: Path,
    *,
    dry_run: bool,
) -> WatchResult:
    release = client.latest_release()
    packaged_version = latest_packaged_version(repository_root)

    if packaged_version >= release.version:
        return WatchResult("up-to-date", packaged_version, release)

    title = issue_title(release)
    if client.issue_exists(title):
        return WatchResult("already-reported", packaged_version, release)

    if dry_run:
        return WatchResult("dry-run", packaged_version, release)

    return WatchResult(
        "issue-created",
        packaged_version,
        release,
        client.create_issue(title, issue_body(release, packaged_version)),
    )


def write_summary(result: WatchResult, summary_path: str | None) -> None:
    if not summary_path:
        return

    lines = (
        "## Noctalia release watcher",
        "",
        f"- Packaged stable version: `{version_text(result.packaged_version)}`",
        f"- Latest upstream release: [`{result.release.tag}`]({result.release.url})",
        f"- Result: `{result.outcome}`",
    )
    if result.issue_url is not None:
        lines += (f"- Created Issue: {result.issue_url}",)

    with Path(summary_path).open("a", encoding="utf-8") as summary:
        summary.write("\n".join(lines) + "\n")


def parse_dry_run(value: str | None) -> bool:
    if value in (None, "", "false"):
        return False
    if value == "true":
        return True
    raise WatcherError("DRY_RUN must be true or false.")


def required_environment(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise WatcherError(f"{name} is not set.")
    return value


def main() -> int:
    try:
        token = required_environment("GH_TOKEN")
        repository = required_environment("GITHUB_REPOSITORY")
        if REPOSITORY_PATTERN.fullmatch(repository) is None:
            raise WatcherError("GITHUB_REPOSITORY has an invalid format.")

        result = watch(
            GitHubClient(token, repository),
            Path.cwd(),
            dry_run=parse_dry_run(os.environ.get("DRY_RUN")),
        )
        write_summary(result, os.environ.get("GITHUB_STEP_SUMMARY"))
        print(f"Watcher result: {result.outcome}.")
        return 0
    except WatcherError as error:
        print(f"Watcher error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
