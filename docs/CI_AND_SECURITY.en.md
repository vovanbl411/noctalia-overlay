# CI and security model

[Русский](CI_AND_SECURITY.md)

## CI model

### Read-only CI

[CI](../.github/workflows/ci.yml) runs on `push` and `pull_request` with
`contents: read`. Its job and required check context is `Python unit tests`.
Keep that context aligned with the ruleset. The job runs code from PRs, so it
uses a read-only token and receives no repository secrets.

Run the same suite from the root of the overlay checkout with Python 3 and Git
on `PATH`. Tests live in `tests/` and run offline without Portage or third-party
Python dependencies.

```bash
python3 -m unittest discover -s tests
```

When the release workflow creates or updates a PR with `GITHUB_TOKEN`,
`pull_request` runs for `opened`, `synchronize`, and `reopened` wait for
approval. A user with write access selects **Approve workflows to run** from
the PR. Confirm that the required check ran for the candidate before merging.
[GitHub workflow triggers](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow)

### Release preparation and publishing

[Prepare Noctalia release](../.github/workflows/noctalia-release-watcher.yml)
runs at 06:17 UTC on `schedule` and supports manual `workflow_dispatch`. The
`noctalia-release-preparation` concurrency group allows one active run at a
time and leaves a running job in place (`cancel-in-progress: false`).

| Stage | Job or owner action |
| --- | --- |
| Discover | `prepare` runs [`watch_noctalia_release.py`](../scripts/watch_noctalia_release.py) to check the stable release, tag, `VERSION`, package policy, and PR conflicts. It fails on incomplete or malformed PR lookup results. |
| Prepare | `prepare` copies the trusted checkout. [`prepare_noctalia_release.py`](../scripts/prepare_noctalia_release.py) adds the candidate ebuild, retires the old fallback, and regenerates both version tables. |
| Validate | `prepare` runs unit tests, verifies source provenance and bytes, generates and checks the Manifest, and runs `pkgcheck scan --exit`. |
| Handoff | [`release_handoff.py`](../scripts/release_handoff.py) packages the checked files in `prepare` and verifies them in `publish`. |
| Publish | `publish` checks the intended remote, current base and PR/branch state, creates the candidate ref, and opens a Draft PR. |
| Accept | The owner reviews and tests the exact candidate, then merges manually. |

The workflow sets `permissions: {}` and grants each job its own permissions.

```text
prepare: contents: read, pull-requests: read
publish: contents: write, pull-requests: write
```

Discover may receive the built-in `GITHUB_TOKEN` with the job's read
permissions through `GH_TOKEN`. Only Publish gets repository write credentials.
Its publication step uses the token for Git authentication and the Draft PR
request. Git credentials stay in the Git command environment. Both watcher
jobs check out `GITHUB_SHA` and verify `HEAD`; all checkouts use
`persist-credentials: false`.

Publish checks repository `vovanbl411/noctalia-overlay`, run ref
`refs/heads/main`, and exact workflow ref
`vovanbl411/noctalia-overlay/.github/workflows/noctalia-release-watcher.yml@refs/heads/main`.
Only `schedule` and `workflow_dispatch` are eligible, with a successful
Prepare job, complete readiness, and dry run disabled. The script verifies
the context case-sensitively and requires the run SHA, checkout `HEAD`, and
handoff base to match.

For `up-to-date` and `already-reported`, source validation, Gentoo and artifact
upload are skipped. A version already accepted on `main` is an ordinary
no-update. `dry_run=true` performs discovery, policy checks, unit tests and,
when applicable, rotation planning. It reports the plan and skips archive
validation, Gentoo, artifact upload and remote writes.

## Trust in upstream Noctalia

The GitHub Release is the primary signal that a stable release was published.
The watcher accepts a public, non-draft, non-prerelease release with a
`vX.Y.Z` tag and the expected `noctalia-dev/noctalia` release URL. A lightweight
tag points directly to a commit. An annotated tag must resolve through its tag
object to a full 40-character lowercase commit SHA.

Before checking overlay policy, the watcher reads `VERSION` through the GitHub
Contents API at that exact commit. The UTF-8 file must match the release
version. The same check applies to the packaged tag before the watcher compares
upstream `PACKAGING.md`, `meson.build`, and `meson_options.txt` by commit.

For annotated tags, the watcher records GitHub's signature metadata.
`verified: true` requires `reason: valid`, a well-formed armored OpenPGP
signature, and a signed payload with matching `object`, `type commit`, and
`tag` headers. Contradictory responses fail. A consistent `verified: false`
response is allowed and its reason is recorded. Lightweight tags report
signature verification as `not applicable`. The owner reviews release notes
and investigates unusual provenance or signing identity during PR review.

## Gentoo container isolation

For an eligible candidate, Prepare downloads the tag archive used by the
ebuild, `https://github.com/noctalia-dev/noctalia/archive/vX.Y.Z.tar.gz`, over
verified HTTPS into a fresh DISTDIR as `noctalia-X.Y.Z.tar.gz`. The verifier
reads the full gzip stream and parses the tar through its terminator. It
compares the Git tar commit ID and PAX comment with the upstream commit
resolved by Discover. Missing, malformed, or mismatching metadata fails the
check. The trust model relies on the authenticated canonical GitHub archive
service for the association between that commit and the archive contents. The
tar commit ID is metadata, without an independent cryptographic proof of the
full upstream Git tree.

Prepare copies the checkout to `$RUNNER_TEMP/noctalia-overlay` without `.git`
or links and verifies that `.git` is absent. The digest-pinned Gentoo container
receives this copy as a writable mount and the isolated DISTDIR as a read-only
mount at `/distfiles`. It gets neither the original `$GITHUB_WORKSPACE` nor
GitHub credentials or secrets. It runs the metadata checks in `/overlay`.

```bash
DISTDIR=/distfiles ebuild "/overlay/gui-apps/noctalia/noctalia-X.Y.Z.ebuild" manifest
emerge --oneshot dev-util/pkgcheck
pkgcheck scan --exit
```

After the container exits, Prepare checks the distfile again. The candidate
DIST entry must match the verified archive's size, BLAKE2B and SHA512. The
retained fallback DIST entry must match the entry in the trusted base.
Manifest generation and verification establish the distfile metadata and byte
binding. `pkgcheck` checks overlay metadata and policy in that Gentoo
environment. The resolver, full Noctalia source build, installation, and
session checks are outside these automated gates.

## Artifact between jobs

The official `actions/upload-artifact` and `actions/download-artifact` are pinned
by full commit SHA.
Prepare uploads `release-handoff-<run_id>-<run_attempt>` with a 30-day artifact
retention period. The archive contains only these files:

```text
release-handoff/
  release.json
  README.md
  README.en.md
  gui-apps/noctalia/Manifest
  gui-apps/noctalia/noctalia-X.Y.Z.ebuild
```

`release.json` uses schema v2. It records the base SHA, release tag and URL,
candidate/fallback/removed versions, deterministic branch and PR title,
upstream packaging changes, and tag provenance. Paths are derived from
validated versions.

Readiness binds the successful Prepare run ID and attempt to the exact upload
artifact ID and SHA-256 digest, validated base, source and candidate identity,
and mandatory gate outcomes. Publish verifies the current run and attempt,
downloads by that ID, and compares the raw ZIP with the upload digest before
extracting it. A Publish-only rerun with a different Prepare attempt fails.
Rerun the entire workflow when the previous attempt cannot supply current
readiness. If a write may already have happened, inspect remote state first.

Before commit, Publish validates the file set, paths, size limits, regular-file
types, schema, provenance and package rotation. It checks the Manifest against
the source evidence and base, the candidate ebuild against the retained
template, and both README files against the generated version tables. The
staged diff must add the candidate ebuild, delete the retired fallback ebuild,
and modify only the Manifest and the two READMEs. It checks staged bytes,
modes, whitespace, and the commit's tree and parent.

The Draft PR links to the exact workflow run and records the Prepare attempt
and validated base SHA. In that attempt's Prepare summary, **Release
readiness** records the complete readiness JSON. The summary and step logs
show the individual outcomes, including source checks, Manifest generation and
verification, and `pkgcheck`. Read the results for the actual candidate.
`passed`, `failed`, `skipped`, `not run`, `not applicable`, and `unknown`
describe different states. A successful job with a skipped gate does not
supply evidence for that gate.

The [audited scheduled run 45663950280](https://github.com/vovanbl411/noctalia-overlay/actions/runs/45663950280)
on `cee4a4fb99cf08a39df1d8aa6613d8a8cf397b2c` had a successful Prepare
job. The GitHub jobs API reports `SKIPPED` for Publish and each Prepare
step listed below.

- `Create sanitized overlay workspace`
- `Prepare the release rotation`
- `Generate Manifest and run pkgcheck`
- `Create release handoff`
- `Upload release handoff`
- `Mark release handoff ready`

That run supplies no hosted candidate evidence for the reviewed implementation.

## Publication and recovery

Publish targets only `vovanbl411/noctalia-overlay`. The candidate branch is
`automation/noctalia-vX.Y.Z` and the PR targets `main`. After local validation,
Publish checks for PR and branch conflicts and compares remote `main` with the
validated base immediately before the first push. The push uses an explicit
expected-absent lease. Only a confirmed new ref qualifies for the Draft PR.
Before requesting it, Publish checks PR conflicts again and rechecks remote
`main` and the candidate head. Git and the GitHub API are separate operations
and changes can occur between checks and writes.

| Situation | Result |
| --- | --- |
| Candidate branch already exists, even at the same commit | Publication stops. Automation does not overwrite or reuse the branch. |
| Conflicting open PR or prior closed PR for the exact candidate head | Publication stops. The owner reviews the PR history. Inconclusive lookups also stop publication. |
| Remote `main` differs from the handoff base | Publication stops before the next write. A stale candidate must be prepared against current `main` before automatic publication. |
| Another actor creates the branch during the push | The expected-absent lease blocks replacement. An up-to-date reply also fails the creation check, and Publish sends no PR request. |
| Push fails or has an ambiguous result | Publish fails without sending a PR request. Read-only checks report the observed ref and exact-head PR state. Branch creation by this run remains unconfirmed. |
| Confirmed push, then the PR request fails or its response is ambiguous | The summary retains the confirmed ref and commit and reports read-back of branch and PR state where available. |
| Candidate head changes before the PR request | Publish stops before sending the request and reports the confirmed write and observed remote state separately. |

Read the Publish summary and logs before retrying. A ref seen at read-back says
where it pointed at that moment. The confirmed-write entry comes from the
create-only push result. Read-back may confirm an open or closed exact-head
PR, report no PR observed, or leave the result `UNKNOWN`. In particular, an
empty read-back after an ambiguous PR request leaves its outcome unknown.

Recovery is manual. Verify branch ownership, the exact candidate commit and
its parent and tree against the validated base and handoff diff, the current
remote `main`, and the complete exact-head PR history. If a matching PR exists,
review it before taking further action. Open a Draft PR manually only for a
verified branch with a current base and resolved conflicts. If abandoning the
candidate, remove only a confirmed orphan branch you own. Resolve a prior
closed-PR conflict separately. Deleting an orphan ref leaves that PR history
in place. Automation performs no retry, deletion, overwrite, or reuse.

## Owner acceptance

1. Confirm the exact PR head SHA and target `main`. Compare the head's parent
   and the ebuild, Manifest and README diff with the validated base and candidate.
2. Review the upstream release notes, provenance and signing identity if
   unusual, and the reported packaging and dependency changes.
3. Open the linked run for the recorded Prepare attempt. Check the base SHA
   and readiness and confirm the applicable gates and the required
   `Python unit tests` result for the candidate.
4. Run `doas emerge -pv gui-apps/noctalia` to check the resolver and overlay
   selection. Install the exact candidate from the PR branch. Record the
   tested version, PR head, profile, architecture, USE and installation method.
   Installing a binary package checks the installed binary. Evidence of a full source
   build requires a recorded build from source for the tested configuration.
5. Test a normal Niri session, including the panel, notifications, networking,
   audio and screen locking. Record the results for that candidate and merge
   manually after the applicable checks pass.

## Ruleset for `main`

On 2026-11-25 UTC, the public [Protect main ruleset](https://github.com/vovanbl411/noctalia-overlay/rules/23505842)
was active for the default branch. The [public rules for `main`](https://api.github.com/repos/vovanbl411/noctalia-overlay/rules/branches/main)
showed that ruleset requiring a PR, resolved review conversations, and the
GitHub Actions check `Python unit tests` (integration ID `15368`) with the
branch up to date. The ruleset blocked deletions and force pushes. It set
required approving reviews to zero. Public evidence does not establish
effective bypass rights or legacy branch protection.

The following is the expected configuration in `Settings → Rules → Rulesets`.

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
required CI, and the owner's manual decision provide protection. This ruleset
requires no second reviewer. Turning on `Restrict updates` with an empty bypass
list can prevent normal PR merges.

## GitHub Actions settings

These are the expected settings in `Settings → Actions → General`.

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

The `actions/...@*` allowlist is separate from SHA pinning. The workflow files
pin every external action. Allowing Actions to create PRs enables Publish to
request a Draft PR. This setting and approval to run the PR's CI are separate.

The owner still needs to confirm repository and inherited settings, including
effective organization and repository rules, legacy branch protection,
additional required checks, bypass and merge access, and whether Actions are
enabled with the expected allowlist, pinning and default token permissions.
Confirm the PR creation setting, approval and retention policy in GitHub. On an
authorized hosted candidate, verify Draft PR creation, approval of CI for the
automation PR, the exact required check on its head, and enforcement when it
is missing or fails. The workflow requests permissions in YAML. GitHub
settings determine which permissions and policies are effective.

## Supply chain and secrets

- GitHub Actions are pinned to full commit SHAs.
- Gentoo Docker images are pinned by SHA256 digest; digests are updated
  manually.
- Dependabot updates only GitHub Actions. Its PRs go through review, CI and
  manual merge.
- `latest`, `@main`, and mutable action tags are not used for security-sensitive
  CI dependencies.
- No PATs or SSH private keys are stored in the repository. Release automation
  uses the built-in `GITHUB_TOKEN`; a long-lived PAT is unnecessary.
- Read-only PR CI receives no repository secrets.

GitHub Secret Scanning and Push Protection should be enabled manually. Their
actual state cannot be determined from the Git repository contents.
