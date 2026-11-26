# CI, security and recovery

[Русский](CI_AND_SECURITY.md) · [Automation walkthrough](AUTOMATION.en.md) ·
[Install the overlay](../README.en.md)

## Checks and evidence

- **Repository CI** runs on `push` and `pull_request`. The
  `Python unit tests` job runs PR code with `contents: read` and without
  repository secrets. This is the required check name in the intended
  settings. For PRs created
  or updated with `GITHUB_TOKEN`, the `opened`, `synchronize` and `reopened`
  events wait for approval. Someone with write access selects
  **Approve workflows to run** on the PR. Check the result for its head.
  [CI](../.github/workflows/ci.yml) · [GitHub trigger rules](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow)
- **Candidate Prepare** checks the source, archive size and hashes,
  Manifest and Gentoo metadata. It also runs unit tests. Dependency
  resolution, full builds, installation and Niri checks are separate
  acceptance steps. [Workflow](../.github/workflows/noctalia-release-watcher.yml)
- **Candidate evidence** is linked from the Draft PR. Check the base SHA,
  Prepare run and attempt, the **Release readiness** JSON and individual
  step logs. `passed`, `failed`, `skipped`, `not run`, `not applicable`
  and `unknown` have distinct meanings. A successful job with skipped
  steps gives evidence for the checks that ran. Full hosted candidate
  validation and pilot acceptance require separate owner confirmation.

## Permissions and publication conditions

The release workflow starts with `permissions: {}`. Prepare gets
`contents: read` and `pull-requests: read`. Publish gets `contents: write`
and `pull-requests: write`. Both check out `GITHUB_SHA` and verify a clean
checkout. Every checkout uses `persist-credentials: false`. Discover
receives the built-in token as `GH_TOKEN`. The publication step uses it
for Git and the Draft PR request. Git authentication stays in the command
environment. The Gentoo container receives a writable temporary copy and
a read-only verified `DISTDIR`. The checkout's `.git` and secrets stay outside.

Prepare and Publish time out after 20 and 10 minutes respectively.
The `noctalia-release-preparation` group allows one active run and keeps
an active run going when another is requested (`cancel-in-progress: false`).
The owner and GitHub can still change branches and PRs between checks.

Publication requires the exact, case-sensitive context
`vovanbl411/noctalia-overlay`, `refs/heads/main` and
`vovanbl411/noctalia-overlay/.github/workflows/noctalia-release-watcher.yml@refs/heads/main`.
Only `schedule` and `workflow_dispatch` are admitted, with successful
Prepare, complete readiness, and dry-run disabled. The checkout,
`GITHUB_SHA` and handoff base must match. Both fetch and push URLs for
`origin` must exactly match `https://github.com/vovanbl411/noctalia-overlay.git`.

### Artifact admission

Prepare uploads `release-handoff-<run_id>-<run_attempt>` for 30 days.
Publish uses the exact ID from readiness for the current run and attempt,
checks the raw ZIP's SHA-256 before extraction, and accepts only the
expected paths, types and sizes. Before a remote write it checks schema v2,
source and rotation, the candidate ebuild against the retained template,
the Manifest, generated README blocks, the staged diff and the commit.
The permitted changes add the candidate ebuild, remove the old fallback,
and update the Manifest and both READMEs. [Handoff contents and provenance](AUTOMATION.en.md#hand-off-the-result)

### Allowed writes

Publish can create an absent `automation/noctalia-vX.Y.Z` branch and a
Draft PR targeting `main`. Before both push and the PR request it checks
conflicting open titles, exact-branch PR history and the current remote
base. Before the PR request it also checks the candidate head against
the confirmed commit. An existing ref blocks the write even at the
same commit. A conflicting open PR, a prior closed PR for the exact
branch, or an inconclusive API result also blocks writing. The owner merges.

## Troubleshooting and recovery

Start with the first required step that did not complete and read its
log. For Publish, read the entire summary before deciding whether to
rerun. CLI errors start with `Release publication error:`.

| Situation | What may have changed and what to inspect |
| --- | --- |
| Discover or rotation stopped | Branches and PRs are unchanged. Check the watcher outcome, tag, `VERSION`, base and two-version policy. `up-to-date`, `already-reported` and dry-run can skip later steps normally. |
| Source, Gentoo or Manifest verification failed | Check the first failed step, expected `SRC_URI`, SHA, archive report, Manifest generation and `pkgcheck` result. A failed gate leaves the candidate ineligible. |
| Upload, readiness, checkout or preflight rejected the candidate | The ZIP may already be stored in Actions. Check the run, attempt, ID, digest and base. A Publish-only rerun with an earlier Prepare attempt is rejected. If no remote write occurred, rerun the full workflow from the intended base. |
| A branch or conflicting PR existed before push, or `main` advanced | Publish stops before writing. Check branch ownership and PR history. A stale base needs a new Prepare on current `main`. |
| Push failed, was ambiguous, or returned `up-to-date` | Publish sends no Draft PR request. Inspect read-back of the ref and exact-branch PRs. Observing the ref does not establish that this run created it. |
| Branch creation was confirmed and a later check or the PR request failed | The branch may remain on GitHub. Check **Last confirmed write**, the ref, the commit and **Exact-head PR state**. An empty read-back after an ambiguous PR request leaves the outcome `UNKNOWN`. |

An absent branch is an observation before a write attempt. An existing
branch needs an owner decision. Only a `[new branch]` push result
confirms that this run created a ref. If another actor creates the ref
between the read and the push, the expected-absent condition protects it
from replacement.

After a possible write, verify branch ownership and the exact head,
parent and tree against the validated base and handoff diff. Then check
current remote `main` and the full PR history for that branch. Review
any PR already present first. Open a Draft PR manually only for a
verified branch with a current base and resolved conflicts. If abandoning
the candidate, delete only a confirmed orphan you own. Deletion preserves
PR history. Resolve a prior closed-PR conflict separately. Automation
retains its create-only policy and performs no retries, deletions,
overwrites or branch reuse.

## Owner acceptance

1. Check the exact PR head, target `main`, commit parent and ebuild,
   Manifest and README diff. Review the upstream release notes,
   provenance, unusual signing details, packaging and dependencies.
2. Open the Prepare attempt named in the PR. Check the base SHA, readiness,
   applicable step results and `Python unit tests` on the exact head.
3. On Gentoo, confirm that the configured overlay points to the candidate
   checkout and `git rev-parse HEAD` there matches the PR head.
   `doas emerge -pv gui-apps/noctalia` previews the plan and repository
   selection. Verify the candidate version and `::noctalia-overlay`
   before installing. Record the head, version, profile, architecture,
   USE and installation method.
4. Install the exact candidate from that checkout. A binpkg installation
   demonstrates binary package installation. Record a source build separately
   when claiming source build evidence. In a normal Niri session, test the
   panel, notifications, networking, audio and screen locking. Record
   the results and merge manually once the applicable checks pass.

## Expected GitHub settings

These are the intended settings. The owner confirms effective settings
and inherited restrictions.

**Rulesets** under `Settings → Rules → Rulesets`.

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

This personal repository sets the number of required approvals to zero in
the intended configuration. Protection comes from the PR, required CI and
the owner's manual decision. `Restrict updates`
with an empty bypass list can block ordinary PR merges.

**Actions** under `Settings → Actions → General`.

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

The allowlist and SHA pinning are separate controls. The workflow pins
Actions to full SHAs and Gentoo images to SHA-256 digests. Image digests
are updated manually. [Dependabot](../.github/dependabot.yml) updates
only GitHub Actions, with review, CI and manual merge for its PRs.
Publication uses the built-in `GITHUB_TOKEN`. The repository keeps no
PATs or SSH private keys.

When verifying effective settings, check organization and repository
rulesets, legacy branch protection, extra checks, bypass and merge
rights, the allowlist, pinning, token permissions, retention, Draft PR
creation and the separate CI-run approval. On an authorized candidate,
confirm the required check appears on the head and merge is blocked
while it is missing or failing. Check Secret Scanning and Push Protection
in GitHub as well. Enabling them is recommended.

## Historical observations

- **Run `45663950280`.** In the [scheduled run](https://github.com/vovanbl411/noctalia-overlay/actions/runs/45663950280)
  at `cee4a4fb99cf08a39df1d8aa6613d8a8cf397b2c`, Prepare succeeded.
  The jobs API showed `SKIPPED` for Publish and for
  `Create sanitized overlay workspace`, `Prepare the release rotation`,
  `Generate Manifest and run pkgcheck`, `Create release handoff`,
  `Upload release handoff` and `Mark release handoff ready`. This evidence
  applies to that run and its skipped steps. It does not establish a
  full hosted candidate run on the reviewed implementation.
- **`main` protection on 2026-11-25 UTC.** The public [Protect main ruleset](https://github.com/vovanbl411/noctalia-overlay/rules/23505842)
  and [branch rules](https://api.github.com/repos/vovanbl411/noctalia-overlay/rules/branches/main)
  showed an active ruleset requiring a PR, resolved conversations, an
  up-to-date base and `Python unit tests` (integration `15368`). It
  blocked deletions and force pushes and required zero approvals. Bypass
  rights and legacy protection remained unconfirmed.
- **Notifications on one workstation.** Noctalia 5.2.1 served
  `org.freedesktop.Notifications`, including Thunderbird notifications.
  On 2026-10-07 the [Gentoo virtual/notification-daemon-0 ebuild](https://github.com/gentoo/gentoo/blob/master/virtual/notification-daemon/notification-daemon-0.ebuild)
  still did not list Noctalia. In that setup, `x11-libs/libnotify` with
  `PDEPEND="virtual/notification-daemon"` could select an X11 daemon
  with `gtk+[X]`, `cairo[X]` and `libXcursor`. The workstation kept
  `x11-libs/libnotify` in world explicitly. Gentoo's Thunderbird ebuild
  offered it through `optfeature "desktop notifications"` without a
  mandatory `RDEPEND`. There was no custom Thunderbird ebuild for it.
  [Original record](https://github.com/vovanbl411/noctalia-overlay/blob/14b374edae214d15876f27c60b246d48f193a57c/README.en.md#L91-L129)
