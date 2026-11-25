# noctalia-overlay

[Русский](README.md)

This overlay tracks stable Noctalia releases without waiting for updates in
GURU. It deliberately does not include a live `9999` version.

`main` keeps exactly two stable Noctalia versions, the current release and the
previous one for rollback. A single release PR adds the candidate, retains the
current release as fallback, removes the old fallback, and regenerates the
version tables in both READMEs. The owner accepts the rotation by merging the PR.

## Contents

<!-- noctalia-versions:start -->
| Package | Purpose |
| --- | --- |
| `gui-apps/noctalia-5.6.0` | Current stable release |
| `gui-apps/noctalia-5.5.0` | Previous release for rollback |
<!-- noctalia-versions:end -->

The overlay uses the main Gentoo repository's eclasses and profiles. The Noctalia ebuilds carry only `KEYWORDS="~amd64"`. Keep
`gui-apps/noctalia ~amd64` in `package.accept_keywords`. The overlay ships no
separate Gentoo profile or validation matrix for other profiles or architectures.
Noctalia is a Wayland shell. The manual release checklist uses Niri.

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

## Installing and rolling back Noctalia

After syncing the overlay and accepting the keyword, install from this repository.

```bash
doas emerge --ask gui-apps/noctalia::noctalia-overlay
```

The ebuild declares one package-specific USE flag, `+jemalloc`. It is enabled
by default, adds `dev-libs/jemalloc:=`, and enables the corresponding Meson
feature. Set `-jemalloc` in `package.use` to disable both. The ebuild also
suggests optional tools for display brightness (`app-misc/ddcutil`), battery
and power devices (`sys-power/upower`), clipboard auto-paste (`gui-apps/wtype`),
and hardware-accelerated recording (`media-video/gpu-screen-recorder`).

To roll back, use the exact fallback version in the table above. Preview the
downgrade before installing. These commands use `5.5.0` as an example.

```bash
doas emerge -pv '=gui-apps/noctalia-5.5.0::noctalia-overlay'
doas emerge --ask --oneshot '=gui-apps/noctalia-5.5.0::noctalia-overlay'
```

Check `noctalia --version` and the Niri scenarios below after switching
versions. A later world update can select the newer release again.

## Portage notification integration

On the reference workstation, Noctalia 5.2.1 provided the D-Bus service
`org.freedesktop.Notifications` and displayed external notifications, including
those from Thunderbird. This observation applies to that version and workstation.

On 2026-10-07, the Gentoo ebuild for
[`::gentoo virtual/notification-daemon-0`](https://github.com/gentoo/gentoo/blob/master/virtual/notification-daemon/notification-daemon-0.ebuild)
did not list Noctalia as a provider. In that setup, `x11-libs/libnotify`
with `PDEPEND="virtual/notification-daemon"` selects another notification daemon.
The default fallback to `x11-misc/notification-daemon` pulls in an X11 stack
through `gtk+[X]`, `cairo[X]`, and `libXcursor`, which is undesirable on a
Wayland-only system.

The local `virtual/notification-daemon-0-r1` preserves upstream providers,
KEYWORDS, and the semantics of the `gnome`/`kde` USE flags, adding
`gui-apps/noctalia` to the fallback OR-group when both `!gnome` and `!kde` apply.
Revision `0-r1` is newer than upstream `0`: with Noctalia installed, Portage can
satisfy the dependency without a second daemon or `package.provided`. This
virtual is maintained manually and does not participate in the rotation of
the two Noctalia versions.

After syncing the overlay and switching to the local virtual, the temporary
`virtual/notification-daemon-0` entry in `/etc/portage/profile/package.provided`
is no longer needed. Remove only that entry and check the resolver:

```bash
emerge -pv virtual/notification-daemon
```

With `USE="-gnome -kde"` and Noctalia installed, Portage should select
`virtual/notification-daemon-0-r1::noctalia-overlay` without
`x11-misc/notification-daemon` or a requirement to enable `USE=X` for `gtk+`
or `cairo`.

On the reference workstation, `x11-libs/libnotify` remains an explicit world
package: the current Gentoo Thunderbird ebuild only suggests it through
`optfeature "desktop notifications"` and has no mandatory `RDEPEND` on it.
A custom Thunderbird ebuild is not maintained for this dependency.

## Checking a release PR

Before merging, follow the [owner acceptance checklist](docs/CI_AND_SECURITY.en.md#owner-acceptance).
Review the exact PR head and `main` base, ebuild and Manifest diff, upstream
changes and provenance, linked Prepare evidence, and required CI result. Run
`doas emerge -pv gui-apps/noctalia`, install the candidate from the PR branch,
and test a regular Niri session. Check the panel, notifications, networking,
audio, and screen locking before merging by hand.

## Release automation

[Prepare Noctalia release](.github/workflows/noctalia-release-watcher.yml) runs
daily at 06:17 UTC and can also be started through `workflow_dispatch`. GitHub
can delay scheduled runs or disable the schedule after 60 days without activity
in a public repository. Use a manual run in either case.

[`scripts/watch_noctalia_release.py`](scripts/watch_noctalia_release.py)
checks the latest stable GitHub release, resolves its tag to a commit, checks
`VERSION` at that commit, and checks for open release PRs. Tag signatures,
when available, add provenance evidence. Unsigned releases can still qualify.
[`scripts/prepare_noctalia_release.py`](scripts/prepare_noctalia_release.py)
copies the current ebuild, retires the previous fallback, and updates both
version tables in an isolated workspace. The workflow verifies the canonical
tag archive, generates and checks the Manifest, and runs `pkgcheck scan --exit`
in Gentoo. The Draft PR shows upstream changes to `PACKAGING.md`, `meson.build`,
and `meson_options.txt`. It also links to the workflow run and records the
verified Prepare attempt.

`dry_run=true` reports discovery, policy checks, and the planned rotation, and
runs the unit tests. It skips archive and Gentoo validation and publication.
Only the Publish job has repository write permissions. It verifies the handoff
and remote base, creates the candidate branch only if it is absent, and opens a
Draft PR for manual acceptance. See the [CI and security guide](docs/CI_AND_SECURITY.en.md)
for provenance, permissions, reruns, and conflict recovery.

Gentoo images are pinned by SHA-256 digest and maintained manually. Dependabot
updates only GitHub Actions.

## Local tests

Run from the root of a checkout of this overlay with Python 3 and Git on
`PATH`. The tests use the Python standard library and disposable local Git
repositories. They run offline without Portage.

```bash
python3 -m unittest discover -s tests
```

The [CI](.github/workflows/ci.yml) workflow runs this unit suite. For manual
changes to `virtual/notification-daemon/`, run `pkgcheck scan` and
`emerge -pv virtual/notification-daemon` in a Gentoo environment. These checks
are separate from general CI and from the Noctalia release rotation.

## Checking after an update

```bash
noctalia --version
```

Also check the panel, notifications, networking, audio, and screen locking in a
regular Niri session.
