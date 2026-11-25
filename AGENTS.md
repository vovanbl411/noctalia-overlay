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

The tree on `main` defines the accepted package state. Use the ebuilds, Manifest,
and repository metadata for packaging facts. The [README](README.en.md)
describes local packaging policy, and the [CI and security guide](docs/CI_AND_SECURITY.en.md)
describes validation and publication. Verify automation behavior against the
workflows and scripts. The pilot targets the Release profile of
[Gentoo Overlay Standard v0.1](https://github.com/vovanbl411/gentoo-overlay-standard/blob/212d0b66de2bb2bba1d9f97cc4f6a156b1e91ef8/STANDARD.md).
Adoption under S2 remains a separate owner decision.

In an ordinary automated release update, copy the current ebuild to the new
stable version, retain the current ebuild as fallback, retire the old fallback,
regenerate the Manifest, and update both generated README version tables.
Get explicit owner approval for changes outside that rotation, including
changes to the virtual package, packaging policy (sources, dependencies, USE,
keywords or retention), automation, required checks, or GitHub settings.

## Build, Test, and Development Commands

Portage builds Noctalia from its ebuild. From the repository root, run the
unit tests with Python 3 and Git on `PATH`. The other commands require Gentoo
with the overlay configured.

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
session before merging the release PR. The owner reviews the exact PR head and
target `main`, the ebuild and Manifest diff, upstream provenance and changes,
the linked run and Prepare attempt, the validated base and readiness summary,
and `Python unit tests` on the candidate. See the
[owner acceptance checklist](docs/CI_AND_SECURITY.en.md#owner-acceptance)
for the resolver, installation, and runtime evidence required before manual merge.

## Commit & Pull Request Guidelines

Recent history uses brief lowercase subjects such as `add: workflow for check
noctalia release` and `update: README`. Use the same `<type>: <summary>` form
where practical. Keep commits narrow. Pull requests should state the packaged
version or watcher behavior changed, link the relevant upstream release or
Issue, list validation performed, and include screenshots only when a visual
change makes them useful.

## Security & Automation

Keep tokens and local Portage configuration out of Git. Discover may use the
built-in token with read permissions. Repository write permissions are confined
to Publish in the trusted default-branch workflow. Publish may create an absent
candidate branch and open a Draft PR against `main`. Existing branches or
conflicting PRs stop publication. Recovery and merge are manual owner actions.
