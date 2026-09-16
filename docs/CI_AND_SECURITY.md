# CI и модель безопасности

Этот документ описывает CI, хранимый в репозитории, и настройки GitHub,
которые владелец задаёт вручную. Файлы в Git не применяют GitHub Settings
автоматически.

## Модель CI

В репозитории есть два независимых workflow.

### Read-only CI

Файл: [`.github/workflows/ci.yml`](../.github/workflows/ci.yml).

Он запускается на `push` и `pull_request`, выполняет Python unit tests и имеет
только:

```yaml
permissions:
  contents: read
```

Имя required status check — `Python unit tests`. Это имя job, а не workflow
`CI`; его не следует переименовывать без изменения ruleset. CI не получает
secrets, не создаёт branches или Pull Requests и не отправляет commits. Код из
pull request может выполняться тестами, поэтому write-capable token здесь не
допустим.

### Подготовка и публикация релиза

Файл:
[`.github/workflows/noctalia-release-watcher.yml`](../.github/workflows/noctalia-release-watcher.yml).

Workflow запускается по `schedule` и вручную через `workflow_dispatch`. На
уровне workflow задано `permissions: {}`. Полномочия выдаются только jobs, где
они нужны:

```text
prepare: contents: read, pull-requests: read
publish: contents: write, pull-requests: write
```

`prepare` выполняет discovery, проверку policy, поиск duplicate PR, расчёт
rotation и unit tests. Он не создаёт branch и PR. `publish` начинается с fresh
checkout `main`, получает проверенный artifact, применяет allowlisted changes,
создаёт deterministic automation branch и открывает Draft PR.

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

Release PR всегда Draft. Workflow никогда не выполняет auto-merge. Candidate
нужно вручную установить из ветки PR и проверить в обычной Niri-сессии.

Для `up-to-date` и `already-reported` Gentoo container не запускается, images
не скачиваются и artifact не создаётся. `dry_run=true` выполняет discovery,
policy, расчёт будущей rotation, tests и summary, но не запускает container,
не создаёт artifact, branch или PR.

## Изоляция Gentoo container

`prepare` создаёт `$RUNNER_TEMP/noctalia-overlay` стандартной библиотекой
Python, копируя checkout без `.git` и без ссылок. Перед запуском container
явно проверяется отсутствие `.git`.

Container получает RW-монтирование только этой sanitized-копии, чтобы
сгенерировать Manifest. Он не получает `$GITHUB_WORKSPACE`, `GH_TOKEN`,
repository credentials или GitHub secrets. В нём выполняются:

```bash
ebuild "/overlay/gui-apps/noctalia/noctalia-X.Y.Z.ebuild" manifest
emerge --oneshot dev-util/pkgcheck
pkgcheck scan --exit
```

До запуска container сохраняется полный снимок содержимого и режимов
sanitized workspace. После его завершения workflow сверяет снимок: измениться
может только `gui-apps/noctalia/Manifest`; добавление, удаление или изменение
любого другого файла либо каталога завершает подготовку до создания artifact.

Если Manifest generation или `pkgcheck` завершается ошибкой, `publish` не
запускается.

## Artifact между jobs

Для handoff используются только официальные
`actions/upload-artifact` и `actions/download-artifact`, закреплённые полным
commit SHA. Artifact хранится около 30 дней.

Он содержит только:

```text
release-handoff/
  release.json
  README.md
  gui-apps/noctalia/Manifest
  gui-apps/noctalia/noctalia-X.Y.Z.ebuild
```

`release.json` содержит schema version, base commit, stable release tag,
candidate/fallback/removed versions, deterministic branch и PR title, URL
релиза, metadata изменений packaging files и минимальную provenance metadata:
tag object SHA, target commit SHA, статус и причину verification, а также
`verified_at`, если GitHub его сообщил. Пути не передаются как данные: они
строятся из уже проверенных версий.

`publish` считает artifact недоверенным input. Перед применением он проверяет
точный список файлов и директорий, regular-file type, отсутствие symlinks и
special files, размер файлов, JSON schema, версии, URL, branch, PR title и
provenance. Неожиданное поле, файл, ссылка или несоответствие версий завершают
job.

Затем `publish` сравнивает base commit с fresh checkout `main`, применяет
только `README.md`, `Manifest` и candidate ebuild, удаляет только ebuild,
построенный из validated removed version, повторно проверяет overlay policy,
выполняет `git diff --check` и сверяет точный allowlist diff. Поэтому artifact
не может изменить `.github/`, `scripts/`, `tests/`, `metadata/`, `profiles/`,
`AGENTS.md`, `.git/` или другой путь.

Token нужен только в `publish` для push deterministic branch и POST Draft PR.
Checkout использует `persist-credentials: false`; authenticated remote URL
восстанавливается сразу после push и не остаётся в `.git/config`.

## Ruleset для `main`

Настраивается вручную: `Settings → Rules → Rulesets`.

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

`Required approvals: 0` выбран намеренно для персонального repository.
Защиту обеспечивает связка PR, обязательного CI и ручного решения владельца;
второй reviewer не обязателен. `Restrict updates` остаётся выключенным: при
пустом bypass list он может запретить нормальный merge Pull Request.

## Настройки GitHub Actions

Настраиваются вручную: `Settings → Actions → General`.

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

Allowlist `actions/...@*` не разрешает использовать mutable tags в workflow.
Отдельная repository policy требует full-length immutable SHA; каждый
внешний Action в YAML должен быть закреплён именно так. Разрешение создавать
Pull Requests нужно только `publish` для Draft PR, а default permissions
остаются read-only.

## Supply chain и secrets

- GitHub Actions закреплены на полный commit SHA.
- Gentoo Docker images закреплены на SHA256 digest; digests обновляются вручную.
- Dependabot обновляет только GitHub Actions; его PR проходят review и CI,
  auto-merge не используется.
- Не используются `latest`, `@main` и mutable action tags для
  security-sensitive CI dependencies.
- PAT и SSH private keys не хранятся в repository. Release automation
  использует встроенный `GITHUB_TOKEN`; long-lived PAT не нужен.
- Read-only PR CI не получает repository secrets.

GitHub Secret Scanning и Push Protection рекомендуется включить вручную.
Их фактическое состояние нельзя определить из содержимого Git repository.

## Доверие к upstream Noctalia

Automation принимает только stable tags `vX.Y.Z`, отвергает prerelease и до
rotation запрашивает GitHub Git Database API. Для candidate требуется
annotated tag: ref должен указывать на tag object, tag object — на commit, а
все Git object SHA должны быть полными 40-символьными lowercase SHA.

GitHub должен сообщить `verification.verified: true` и
`verification.reason: valid`. Automation также проверяет OpenPGP armored
signature и canonical header lines signed payload: `object <commit>`, `type
commit`, `tag <release-tag>`. Lightweight tags, unsigned tags, противоречивые
payload и любой malformed response отклоняются до container, artifact, branch
и Draft PR.

Для packaging diff проверяются как candidate tag, так и tag текущей packaged
версии. Файлы `PACKAGING.md`, `meson.build` и `meson_options.txt` сравниваются
по verified immutable commit SHA, а не по mutable tag name. Provenance видна в
workflow summary и Draft PR без signature block или tag message.

Эта модель доверяет результату проверки GitHub. Оверлей пока не выполняет
независимую OpenPGP verification и не закрепляет fingerprint ключа maintainer.
Manual review release notes и signing identity остаётся обязательной, если
provenance выглядит необычно.

Следующий отдельный этап hardening — independent cryptographic verification,
trusted signer fingerprint pinning и затем Portage-side verification на Gentoo
машине.
