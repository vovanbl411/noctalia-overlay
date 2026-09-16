"""Policy helpers for the two-version Noctalia overlay."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


Version = tuple[int, int, int]
EBUILD_PATTERN = re.compile(r"^noctalia-(\d+)\.(\d+)\.(\d+)\.ebuild$")
VERSION_BLOCK_PATTERN = re.compile(
    r"(?P<start><!-- noctalia-versions:start -->)"
    r".*?"
    r"(?P<end><!-- noctalia-versions:end -->)",
    re.DOTALL,
)
README_VERSION_PATTERN = re.compile(r"`gui-apps/noctalia-(\d+\.\d+\.\d+)`")


class OverlayPolicyError(RuntimeError):
    """Raised when the overlay does not satisfy its release policy."""


@dataclass(frozen=True, order=True)
class StableEbuild:
    """A stable ebuild and its parsed version."""

    version: Version
    path: Path


@dataclass(frozen=True)
class OverlayState:
    """The stable ebuild pair required on the main branch."""

    fallback: StableEbuild
    current: StableEbuild


def parse_version(value: str) -> Version:
    """Parse a strict X.Y.Z version string."""
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", value)
    if match is None:
        raise OverlayPolicyError(f"Not a stable Noctalia version: {value}.")
    return tuple(map(int, match.groups()))


def version_text(version: Version) -> str:
    """Return a Version tuple in ebuild filename form."""
    return ".".join(map(str, version))


def stable_ebuilds(repository_root: Path) -> list[StableEbuild]:
    """Return only ebuilds whose names use the strict stable version format."""
    package_dir = repository_root / "gui-apps" / "noctalia"
    ebuilds = []
    for path in package_dir.glob("noctalia-*.ebuild"):
        match = EBUILD_PATTERN.fullmatch(path.name)
        if match is not None:
            ebuilds.append(StableEbuild(tuple(map(int, match.groups())), path))
    return sorted(ebuilds)


def overlay_state(repository_root: Path) -> OverlayState:
    """Validate the two-version policy and return fallback/current ebuilds."""
    ebuilds = stable_ebuilds(repository_root)
    if len(ebuilds) != 2:
        raise OverlayPolicyError(
            "Expected exactly two stable Noctalia ebuilds, "
            f"found {len(ebuilds)}."
        )
    # stable_ebuilds() сортирует семантические версии, поэтому этот порядок
    # задаёт политику двух версий и не зависит от порядка обхода файловой системы.
    return OverlayState(fallback=ebuilds[0], current=ebuilds[1])


def rendered_version_block(state: OverlayState) -> str:
    """Render the README fragment that documents the packaged versions."""
    return "\n".join(
        (
            "<!-- noctalia-versions:start -->",
            "| Пакет | Назначение |",
            "| --- | --- |",
            "| "
            f"`gui-apps/noctalia-{version_text(state.current.version)}` "
            "| Текущий стабильный релиз |",
            "| "
            f"`gui-apps/noctalia-{version_text(state.fallback.version)}` "
            "| Предыдущая версия для отката |",
            "<!-- noctalia-versions:end -->",
        )
    )


def readme_matches_state(readme_path: Path, state: OverlayState) -> bool:
    """Check that the dedicated README version table matches the ebuild pair."""
    match = VERSION_BLOCK_PATTERN.search(readme_path.read_text(encoding="utf-8"))
    if match is None:
        return False
    documented = README_VERSION_PATTERN.findall(match.group())
    return documented == [
        version_text(state.current.version),
        version_text(state.fallback.version),
    ]


def validate_overlay(repository_root: Path) -> OverlayState:
    """Validate both ebuild policy and the README's version table."""
    state = overlay_state(repository_root)
    readme_path = repository_root / "README.md"
    if not readme_matches_state(readme_path, state):
        raise OverlayPolicyError(
            "README.md does not match the current/fallback Noctalia ebuilds."
        )
    return state


def update_readme_versions(readme_path: Path, state: OverlayState) -> None:
    """Replace the dedicated README version table with the supplied state."""
    text = readme_path.read_text(encoding="utf-8")
    replacement, count = VERSION_BLOCK_PATTERN.subn(
        rendered_version_block(state), text
    )
    if count != 1:
        raise OverlayPolicyError(
            "README.md must contain exactly one Noctalia version table."
        )
    readme_path.write_text(replacement, encoding="utf-8")
