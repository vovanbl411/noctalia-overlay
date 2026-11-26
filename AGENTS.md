# Repository Guidelines

## Sources of truth and scope

- `main` is the accepted package state. Read ebuilds, `Manifest`, `metadata/`
  and `profiles/` for packaging facts. Check the [release workflow](.github/workflows/noctalia-release-watcher.yml)
  and scripts for automation behavior. Unit tests live in `tests/`.
- The pilot targets the Release profile of [Gentoo Overlay Standard v0.1](https://github.com/vovanbl411/gentoo-overlay-standard/blob/212d0b66de2bb2bba1d9f97cc4f6a156b1e91ef8/STANDARD.md).
  Adoption under S2 requires a separate owner decision. The
  [v0.2 draft](https://github.com/vovanbl411/gentoo-overlay-standard/blob/b90ec059d72fd5d55e9b0f1e47ba11acac7089b0/STANDARD.md)
  is a review reference for S3.
- Keep exactly two stable `noctalia-X.Y.Z.ebuild` files in
  `gui-apps/noctalia/`, with `EAPI=8` and `~amd64`. Do not add `9999`.
  In an ordinary release update, copy the current ebuild to the new version,
  keep the previous current ebuild as fallback, retire the old fallback,
  regenerate the Manifest and update both generated README blocks.
- The manually maintained `virtual/notification-daemon` stays outside this
  rotation. Preserve its upstream EAPI, KEYWORDS and provider semantics.
  It has no `SRC_URI` and needs no Manifest with `thin-manifests = true`.
- Get explicit owner approval for changes outside routine rotation, including
  the virtual, packaging policy (sources, dependencies, USE, keywords and
  retention), automation, required checks and GitHub settings.

## Automation modules

- [overlay_policy.py](scripts/overlay_policy.py) reads state and verifies packaging policy.
- [watch_noctalia_release.py](scripts/watch_noctalia_release.py) discovers releases and provides the GitHub API client.
- [prepare_noctalia_release.py](scripts/prepare_noctalia_release.py) plans and applies the package rotation.
- [release_handoff.py](scripts/release_handoff.py) verifies source, Manifest, readiness and candidate files.
- [publish_noctalia_release.py](scripts/publish_noctalia_release.py) handles privileged publication.

Use the Python standard library, four-space indentation, type annotations,
`snake_case` for functions and variables and `PascalCase` for classes. Keep
GitHub API requests in `GitHubClient`, failure messages specific and decision
logic testable with a fake client. In ebuilds, use tabs in functions and keep
dependency blocks and metadata readable.

## Verification and acceptance

- After automation changes, run `python3 -m unittest discover -s tests`
  offline from the repository root with Python 3 and Git on `PATH`. Add
  focused `test_<behavior>` methods for changed behavior.
- For ebuild or Manifest changes, regenerate or refresh the candidate Manifest
  from the downloaded upstream archive, run `pkgcheck scan` in Gentoo and
  preview resolution with `doas emerge -pv gui-apps/noctalia`. For virtual
  changes, use `pkgcheck scan` and `emerge -pv virtual/notification-daemon`.
  That validation stops at the install plan.
- Keep tokens and local Portage configuration out of Git. Discover uses
  the built-in read token. Only trusted Publish writes to GitHub. It may
  create an absent candidate ref and a Draft PR against `main`. Conflicts
  stop publication. The owner handles recovery and merge.
- Follow the [owner acceptance checklist](docs/CI_AND_SECURITY.en.md#owner-acceptance)
  for evidence on the exact PR head, the required CI check, resolver and installed
  Niri session checks. Use the [automation walkthrough](docs/AUTOMATION.en.md)
  to trace the workflow and the [user guide](README.en.md) for overlay use.
- Use short `<type>: <summary>` commit subjects and keep changes narrow.
  PR descriptions identify the package version or automation behavior,
  link relevant upstream work and record checks. Include screenshots for
  visual changes where useful.
