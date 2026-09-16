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

Before the container starts, the workflow saves a complete snapshot of the
sanitized workspace contents and modes. After it exits, the workflow compares
the snapshot: only `gui-apps/noctalia/Manifest` may change. Adding, removing,
or changing any other file or directory stops preparation before artifact
creation.

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
URL, metadata for packaging-file changes, and minimal provenance metadata: tag
object SHA, target commit SHA, verification status and reason, and `verified_at`
when GitHub provides it. Paths are not passed as data; they are constructed from
validated versions.

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

The automation accepts only stable `vX.Y.Z` tags, rejects prereleases, and
queries the GitHub Git Database API before rotation. A candidate requires an
annotated tag: the ref must point to a tag object, the tag object must point to
a commit, and all Git object SHAs must be complete 40-character lowercase SHAs.

GitHub must report `verification.verified: true` and
`verification.reason: valid`. The automation also checks the armored OpenPGP
signature and canonical header lines in the signed payload: `object <commit>`,
`type commit`, and `tag <release-tag>`. Lightweight tags, unsigned tags,
contradictory payloads, and malformed responses are rejected before the
container, artifact, branch, or Draft PR.

For the packaging diff, both the candidate tag and the tag of the current
packaged version are checked. `PACKAGING.md`, `meson.build`, and
`meson_options.txt` are compared by verified immutable commit SHA rather than a
mutable tag name. Provenance appears in the workflow summary and Draft PR
without the signature block or tag message.

This trust model relies on GitHub's verification result. The overlay does not
yet perform independent OpenPGP verification or pin the maintainer's key
fingerprint. Manual review of release notes and signing identity remains
required when provenance looks unusual.

The next separate hardening step is independent local OpenPGP verification. It
will add the trusted upstream public key to the repository and pin its full
fingerprint. A local GPG helper will verify the imported key fingerprint, exact
signature, and signed payload of the already obtained tag without allowing
network key retrieval. GitHub verification will remain a second required check.

After successful local verification, the handoff will contain
`local_signature_verified: true` and the complete `signer_fingerprint`; both
fields will appear in the workflow summary and Draft PR. Any mismatch must fail
closed before Docker. A separate Portage-side verification on the Gentoo machine
can follow.
