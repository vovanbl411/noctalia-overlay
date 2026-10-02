# CI and security model

[Русский](CI_AND_SECURITY.md)

## CI model

The repository has two independent workflows.

### Read-only CI

File: [`.github/workflows/ci.yml`](../.github/workflows/ci.yml).

It runs on `push` and `pull_request`, executes the Python unit tests, and has
only:

```yaml
permissions:
  contents: read
```

The required status check is named `Python unit tests`. This is the job name,
not the `CI` workflow name, and must not be renamed without updating the
ruleset. CI receives no secrets, does not create branches or pull requests, and
does not push commits. Pull-request code can run in tests, so a write-capable
token is not permitted here.

### Release preparation and publishing

File:
[`.github/workflows/noctalia-release-watcher.yml`](../.github/workflows/noctalia-release-watcher.yml).

The workflow runs on `schedule` and can be started manually through
`workflow_dispatch`. It sets `permissions: {}` at workflow level. Permissions
are granted only to the jobs that need them:

```text
prepare: contents: read, pull-requests: read
publish: contents: write, pull-requests: write
```

`prepare` performs discovery, policy validation, duplicate-PR lookup, rotation
planning, and unit tests. It does not create a branch or PR. `publish` starts
from a fresh checkout of `main`, receives a verified artifact, applies only
allowlisted changes, creates a deterministic automation branch, and opens a
Draft PR.

```text
upstream release
    ↓
prepare: discovery, policy, tests
    ↓
sanitized workspace → Manifest/pkgcheck
    ↓
minimal release-handoff artifact
    ↓
publish: fresh main checkout and validation
    ↓
automation branch → Draft Pull Request
    ↓
manual verification → merge
```

Release PRs are always Draft. The workflow never auto-merges. The candidate
must be manually installed from the PR branch and tested in a regular Niri
session.

For `up-to-date` and `already-reported`, the Gentoo container does not start,
images are not downloaded, and no artifact is created. `dry_run=true` performs
discovery, policy validation, planned rotation, tests, and summary generation,
but does not start the container or create an artifact, branch, or PR.

## Gentoo container isolation

`prepare` creates `$RUNNER_TEMP/noctalia-overlay` with the Python standard
library, copying the checkout without `.git` or links. It explicitly checks
that `.git` is absent before starting the container.

The container receives read-write access only to this sanitized copy to generate
the Manifest. It does not receive `$GITHUB_WORKSPACE`, `GH_TOKEN`, repository
credentials, or GitHub secrets. It runs:

```bash
ebuild "/overlay/gui-apps/noctalia/noctalia-X.Y.Z.ebuild" manifest
emerge --oneshot dev-util/pkgcheck
pkgcheck scan --exit
```

If Manifest generation or `pkgcheck` fails, `publish` does not run.

## Artifact between jobs

The handoff uses only the official `actions/upload-artifact` and
`actions/download-artifact`, pinned to full commit SHAs. The artifact is kept
for about 30 days.

It contains only:

```text
release-handoff/
  release.json
  README.md
  README.en.md
  gui-apps/noctalia/Manifest
  gui-apps/noctalia/noctalia-X.Y.Z.ebuild
```

`release.json` contains the schema version, base commit, stable release tag,
candidate/fallback/removed versions, deterministic branch and PR title, release
URL, metadata for packaging-file changes, and provenance metadata: tag type,
optional tag object SHA, target commit SHA, verification status and reason, and
`verified_at` when GitHub provides it. Paths are not passed as data; they are
constructed from validated versions.

`publish` treats the artifact as untrusted input. Before applying it, it checks
the exact file and directory list, regular-file type, absence of symlinks and
special files, file sizes, JSON schema, versions, URL, branch, PR title, and
provenance. An unexpected field, file, link, or version mismatch fails the job.

It then compares the base commit with a fresh checkout of `main`, applies only
`README.md`, `README.en.md`, `Manifest`, and the candidate ebuild, removes only
the ebuild constructed from the validated removed version, revalidates the
overlay policy, runs `git diff --check`, and compares the diff to the exact
allowlist. The artifact therefore cannot modify `.github/`, `scripts/`,
`tests/`, `metadata/`, `profiles/`, `AGENTS.md`, `.git/`, or any other path.

The token is needed only in `publish` to push the deterministic branch and POST
the Draft PR. Checkout uses `persist-credentials: false`; the authenticated
remote URL is restored immediately after push and does not remain in
`.git/config`.

## Ruleset for `main`

Configure manually: `Settings → Rules → Rulesets`.

```text
Ruleset: Protect main
Enforcement: Active
Target: Default branch
Bypass list: empty

Restrict deletions: ON
Restrict updates: OFF
Block force pushes: ON

Require pull request before merging: ON
Required approvals: 0
Require conversation resolution: ON

Require status checks: ON
Required check: Python unit tests
Require branches to be up to date: ON

Require signed commits: OFF for now
Require linear history: OFF
Merge queue: OFF
```

`Required approvals: 0` is intentional for this personal repository. The PR,
required CI, and the owner's manual decision provide protection; a second
reviewer is not required. `Restrict updates` remains off: with an empty bypass
list, it can prevent normal pull-request merging.

## GitHub Actions settings

Configure manually: `Settings → Actions → General`.

```text
Actions permissions:
Allow vovanbl411, and select non-vovanbl411, actions and reusable workflows

Allowed external actions:
actions/checkout@*
actions/upload-artifact@*
actions/download-artifact@*

Require actions to be pinned to a full-length commit SHA: ON
Default workflow permissions: Read repository contents and packages permissions
Allow GitHub Actions to create and approve pull requests: ON
Require approval for all external contributors: ON
Artifact and log retention: about 30 days
```

The `actions/...@*` allowlist does not permit mutable tags in the workflow. A
separate repository policy requires a full-length immutable SHA, and every
external action in YAML must use one. The pull-request permission is needed only
by `publish` for Draft PRs; default permissions remain read-only.

## Supply chain and secrets

- GitHub Actions are pinned to full commit SHAs.
- Gentoo Docker images are pinned by SHA256 digest; digests are updated
  manually.
- Dependabot updates only GitHub Actions; its PRs go through review and CI, and
  auto-merge is not used.
- `latest`, `@main`, and mutable action tags are not used for security-sensitive
  CI dependencies.
- No PATs or SSH private keys are stored in the repository. Release automation
  uses the built-in `GITHUB_TOKEN`; a long-lived PAT is unnecessary.
- Read-only PR CI receives no repository secrets.

GitHub Secret Scanning and Push Protection should be enabled manually. Their
actual state cannot be determined from the Git repository contents.

## Trust in upstream Noctalia

The GitHub Release is the primary signal that a stable release was published.
The automation accepts only a public, non-prerelease release with a `vX.Y.Z` tag
and the expected `noctalia-dev/noctalia` URL. The tag must resolve to a full
40-character lowercase commit SHA: a lightweight tag may point directly to a
commit, while an annotated tag must point to a commit through its tag object.

Before checking overlay policy, the automation reads `VERSION` through the
GitHub Contents API with `ref` set to the exact target commit SHA. The file must
be UTF-8 and contain exactly the release version. A missing, malformed, or
mismatching `VERSION` fails the run. Before the packaging diff, the same check
runs for the exact commit resolved from the currently packaged tag.
`PACKAGING.md`, `meson.build`, and `meson_options.txt` are compared by these
commit SHAs rather than mutable tag names.

Tag signatures are additional provenance metadata, not an acceptance gate. For
annotated tags, the automation records GitHub's response. If GitHub reports
`verified: true`, `reason: valid`, a correctly formed armored OpenPGP signature,
and a signed payload with matching `object`, `type commit`, and `tag` headers
are required. Contradictory verified responses fail. A `verified: false`
response does not by itself block a stable release; its reason is preserved
and the signature is not presented as verified. Lightweight tags report
signature verification as `not applicable`. The summary and Draft PR show the
actual provenance.

Manual review of release notes and upstream provenance/signing identity remains
part of Draft PR review where applicable.
