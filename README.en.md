# noctalia-overlay

[Русский](README.md)

This Gentoo overlay makes stable Noctalia releases available before they
reach GURU. Noctalia is a Wayland shell. This overlay uses Niri for manual
release checks.

## Packages

<!-- noctalia-versions:start -->
| Package | Purpose |
| --- | --- |
| `gui-apps/noctalia-5.6.0` | Current stable release |
| `gui-apps/noctalia-5.5.0` | Previous release for rollback |
<!-- noctalia-versions:end -->

`main` keeps exactly two stable Noctalia ebuilds. There is no live `9999`
ebuild. The separate `virtual/notification-daemon` package lets Portage
count an installed Noctalia as a notification provider under specific USE
settings.

## Adding the overlay

Create `/etc/portage/repos.conf/noctalia-overlay.conf` with these settings.

```ini
[noctalia-overlay]
location = /var/db/repos/noctalia-overlay
sync-type = git
sync-uri = https://github.com/vovanbl411/noctalia-overlay.git
auto-sync = yes
priority = 50
```

```bash
doas emaint sync -r noctalia-overlay
```

`-r` selects this repository. Portage clones it to the specified location
on the first run and updates the local copy on later runs. With
`auto-sync = yes`, explicit runs of `doas emerge --sync` or
`doas emaint sync --auto` also sync the overlay. Priority `50` normally
ranks it above GURU. Keep GURU enabled for other packages.

## Installation and USE flags

The overlay uses eclasses and profiles from the main Gentoo repository.
The Noctalia ebuilds carry only the `~amd64` keyword. Add `gui-apps/noctalia ~amd64` to
`package.accept_keywords`.

```bash
doas emerge -pv gui-apps/noctalia
doas emerge --ask gui-apps/noctalia::noctalia-overlay
```

The first command shows the installation plan and dependencies. In `-pv`,
`-p` previews the plan and `-v` shows details. Check that Portage selected `::noctalia-overlay`.
In the second command, that suffix selects the package source and
`--ask` prompts for confirmation before installation.

The `+jemalloc` USE flag is enabled by default. It adds the
`dev-libs/jemalloc:=` dependency and enables the corresponding Meson
feature. Set `gui-apps/noctalia -jemalloc` in `package.use` to disable both.
The [ebuild](gui-apps/noctalia/) also suggests optional tools for display
brightness (`app-misc/ddcutil`), power (`sys-power/upower`), clipboard
auto-paste (`gui-apps/wtype`), and hardware-accelerated screen recording
(`media-video/gpu-screen-recorder`).

## Notifications

The local [virtual](virtual/notification-daemon/) preserves Gentoo's
providers, KEYWORDS, and rules for the `gnome` and `kde` USE flags. With
both flags disabled, it adds `gui-apps/noctalia` to the list of eligible
providers. An installed Noctalia can satisfy the dependency without a
second daemon or a `package.provided` entry. This package is maintained
manually.

```bash
emerge -pv virtual/notification-daemon
```

With `USE="-gnome -kde"` and Noctalia installed, expect the plan to select
`virtual/notification-daemon-0-r1::noctalia-overlay` without installing
`x11-misc/notification-daemon` or requiring `USE=X` for `gtk+` and
`cairo`. If a temporary `virtual/notification-daemon-0` entry in
`/etc/portage/profile/package.provided` remains after switching to this
virtual, remove only that entry and preview the plan again. [Historical
D-Bus and Thunderbird observation](docs/CI_AND_SECURITY.en.md#historical-observations)

## Updates and rollback

Sync the overlay as during setup, then repeat the installation commands.
To roll back, substitute the version from the “Previous release for
rollback” row in the package table.

```bash
doas emerge -pv '=gui-apps/noctalia-<fallback-version>::noctalia-overlay'
doas emerge --ask --oneshot '=gui-apps/noctalia-<fallback-version>::noctalia-overlay'
```

`=` selects the exact version. Preview the downgrade, then confirm the
installation. `--oneshot` leaves the world set intact. A later world update
can select the current version again.

After an update or rollback, check `noctalia --version`, then test the
panel, notifications, networking, audio and screen locking in a normal
Niri session.

## Validation and maintenance

Repository CI runs the automation unit tests. A full candidate preparation
run checks the archive, Manifest and Gentoo metadata. Dependency resolution,
build and session checks are performed separately. The overlay claims no
validation matrix for other profiles or architectures. For evidence about
a specific candidate, see its PR and Prepare report.

The automation prepares the rotation and opens a Draft PR for a new stable
release. The owner reviews the candidate and accepts it by merging manually.
Maintainers can use the [automation walkthrough](docs/AUTOMATION.en.md) and
the [CI, security and recovery reference](docs/CI_AND_SECURITY.en.md).
