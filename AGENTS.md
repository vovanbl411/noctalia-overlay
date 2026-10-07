# Repository Guidelines

## Project Structure & Module Organization

This is a personal Gentoo overlay for stable Noctalia releases and notification
integration. Noctalia package definitions and their `Manifest` live in
`gui-apps/noctalia/`; name each new Noctalia ebuild `noctalia-X.Y.Z.ebuild`.
Repository metadata is in `metadata/` and `profiles/`. The GitHub release watcher is
`scripts/watch_noctalia_release.py`; policy and preparation helpers are also
in `scripts/`. Unit tests are in `tests/`. The scheduled workflow is under
`.github/workflows/`.

`virtual/notification-daemon/` is a small, manually maintained integration
package that preserves upstream virtual EAPI, KEYWORDS, and semantics.
It has no `SRC_URI` and needs no Manifest with `thin-manifests = true`.
The release watcher and two-version rotation manage only `gui-apps/noctalia/`;
the virtual does not participate in that rotation.

Do not add a `9999` ebuild. `gui-apps/noctalia/` deliberately contains exactly
two stable, versioned releases for `~amd64`: current and fallback.

## Build, Test, and Development Commands

There is no standalone build step: Portage builds the package from its ebuild.

```bash
python3 -m unittest discover -s tests
pkgcheck scan
doas emerge -pv gui-apps/noctalia
```

Run the first command after editing the automation. Run `pkgcheck scan` after
an ebuild or Manifest change, then use the `emerge` preview to verify
dependency resolution and that Portage selects this overlay. Generate or
refresh the Noctalia Manifest from the downloaded upstream release archive
before submitting a new Noctalia ebuild. For manual changes to
`virtual/notification-daemon/`, run `pkgcheck scan` locally and
`emerge -pv virtual/notification-daemon` to verify overlay selection and
dependency resolution; do not install packages as part of this validation.

## Coding Style & Naming Conventions

Keep Python compatible with the standard library only. Follow the existing
style: four-space indentation, type annotations, `snake_case` for functions
and variables, `PascalCase` for classes, and clear `WatcherError` messages.
Keep network access in `GitHubClient` and make decision logic testable with a
fake client. Retain `EAPI=8` for `gui-apps/noctalia/` and upstream EAPI for
`virtual/notification-daemon/`. Follow Gentoo ebuild conventions: use tabs in
ebuild functions and keep dependency blocks and metadata readable.

## Testing Guidelines

Tests use Python's `unittest` and need no network access. Add focused methods
named `test_<behavior>` for each branch changed in discovery, policy, or
preparation—for example, stable-tag validation, rotation, and duplicate-PR
handling. For package updates, also manually test the installed Noctalia
session before merging the release PR.

## Commit & Pull Request Guidelines

Recent history uses brief lowercase subjects such as `add: workflow for check
noctalia release` and `update: README`. Use the same `<type>: <summary>` form
where practical. Keep commits narrow. Pull requests should state the packaged
version or watcher behavior changed, link the relevant upstream release or
Issue, list validation performed, and include screenshots only when a visual
change makes them useful.

## Security & Automation

Do not commit tokens or local Portage configuration. The release watcher reads
`GH_TOKEN` and `GITHUB_REPOSITORY` from its environment; use the GitHub Actions
token for workflow runs and avoid printing either value. The workflow prepares
a Draft PR but must not merge it or install Noctalia automatically.
