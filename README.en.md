# noctalia-overlay

[Русский](README.md)

This overlay tracks stable Noctalia releases without waiting for updates in
GURU. It deliberately does not include a live `9999` version.

`main` always contains exactly two stable Noctalia versions: the current
release and the previous release for quick rollback. Rotation happens atomically
in a release PR: the new version is added, the current one becomes the fallback,
and the old fallback is removed. `main` remains unchanged until the PR is merged.

## Contents

<!-- noctalia-versions:start -->
| Package | Purpose |
| --- | --- |
| `gui-apps/noctalia-5.1.0` | Current stable release |
| `gui-apps/noctalia-5.0.1` | Previous release for rollback |
<!-- noctalia-versions:end -->

The overlay inherits eclasses from the main Gentoo repository. Noctalia keeps
`KEYWORDS="~amd64"`, so an active `gui-apps/noctalia ~amd64` entry in
`package.accept_keywords` is still required.

For the CI design, branch protection, and repository security settings, see
[`docs/CI_AND_SECURITY.en.md`](docs/CI_AND_SECURITY.en.md).

## Adding and syncing the overlay

File: `/etc/portage/repos.conf/noctalia-overlay.conf`

```ini
[noctalia-overlay]
location = /var/db/repos/noctalia-overlay
sync-type = git
sync-uri = https://github.com/vovanbl411/noctalia-overlay.git
auto-sync = yes
priority = 50
```

After saving the configuration, run the first sync:

```bash
doas emaint sync -r noctalia-overlay
```

Portage clones the repository to `/var/db/repos/noctalia-overlay` when the
directory does not yet exist. Subsequent invocations fetch changes from Git.

Confirm that Portage selects this overlay rather than GURU:

```bash
doas emerge -pv gui-apps/noctalia
```

The overlay has a higher priority than GURU, but GURU should remain enabled
because it provides other packages.

`auto-sync = yes` includes the overlay in both `doas emerge --sync` and
`doas emaint sync --auto`. It does not create a background timer: syncing only
happens when one of those commands is explicitly run.

## Checking a release PR

Before merging a Draft PR:

1. Confirm that the release has a stable `vX.Y.Z` tag; prereleases and `main`
   are not used. Before preparing a candidate, the automation verifies that the
   tag is annotated and that GitHub confirms its OpenPGP signature.
2. Review the release notes, provenance and signing identity if anything looks
   unusual, and check upstream changes to `PACKAGING.md`.
3. Review the generated ebuild and Manifest, the `pkgcheck scan` result, and
   run `doas emerge -pv gui-apps/noctalia`.
4. Install the candidate from the PR branch and test a regular Niri session.
5. Check the panel, notifications, networking, audio, and screen locking. Merge
   the Draft PR only after the manual checks succeed.

## Release automation

The [Prepare Noctalia release](.github/workflows/noctalia-release-watcher.yml)
workflow runs daily at 06:17 UTC and can also be started manually through
`workflow_dispatch`.

```text
upstream release
      ↓
signed annotated tag verification
      ↓
packaging diff
      ↓
Draft PR
      ↓
manual review + Niri test
      ↓
merge
      ↓
candidate becomes current, previous current becomes fallback
```

[`scripts/watch_noctalia_release.py`](scripts/watch_noctalia_release.py)
requests the latest release through the GitHub API, accepts only strict
`vX.Y.Z` tags, validates the two-version policy, and looks for an existing open
PR for that tag. A repeated scheduled run therefore does not create a duplicate
PR. For a new release,
[`scripts/prepare_noctalia_release.py`](scripts/prepare_noctalia_release.py)
copies the current ebuild to make the candidate, removes the former fallback,
and updates the version tables above. The workflow then regenerates the
Manifest in a pinned Gentoo container, runs `pkgcheck scan --exit`, and opens a
Draft PR. The PR separately reports whether upstream `PACKAGING.md`,
`meson.build`, and `meson_options.txt` changed.

The `dry_run` parameter runs discovery, policy validation, and the planned
rotation, and writes a summary, but does not modify the workspace or create a
branch, commit, or PR. The read-only `prepare` job has no write permissions.
Only `publish` receives `contents: write` and `pull-requests: write`, after
successful preparation of the minimal release artifact. It does not auto-merge
or install the package on a user's machine. GitHub can delay a scheduled run;
in a public repository, schedules are disabled after 60 days of inactivity. In
both cases the workflow can be started manually.

To allow the workflow to create Draft PRs, the repository owner must enable
`Settings → Actions → General → Workflow permissions → Allow GitHub Actions to create and approve pull requests`.
The workflow does not change this setting.

Gentoo Docker images are pinned by SHA256 digest. Dependabot updates only
GitHub Actions, so image digests must be checked and updated manually from time
to time.

The scripts use only the Python standard library. Unit tests in `tests/` do not
require GitHub access; CI checks `pkgcheck` and the Manifest.

## Checking after an update

```bash
noctalia --version
```

Also check the panel, notifications, networking, audio, and screen locking in a
regular Niri session.
