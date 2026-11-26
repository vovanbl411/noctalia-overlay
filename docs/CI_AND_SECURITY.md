# CI, безопасность и восстановление

[English](CI_AND_SECURITY.en.md) · [Как устроена автоматизация](AUTOMATION.md) ·
[Установка оверлея](../README.md)

## Проверки и доказательства

- **CI репозитория** запускается на `push` и `pull_request`. Задание
  `Python unit tests` выполняет код из PR с `contents: read` и без
  секретов репозитория. Это имя обязательной проверки в целевой
  конфигурации. Для PR от
  `GITHUB_TOKEN` события `opened`, `synchronize` и `reopened` ожидают
  одобрения запуска. Пользователь с правом записи выбирает
  **Approve workflows to run** в PR. Проверяйте результат для его head.
  [CI](../.github/workflows/ci.yml) · [Правила запуска GitHub](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow)
- **Prepare кандидата** проверяет исходник, размер и хеши архива, Manifest
  и метаданные Gentoo. Unit-тесты входят и в Prepare. Resolver, полная
  сборка, установка и Niri остаются отдельными действиями приёмки.
  [Workflow](../.github/workflows/noctalia-release-watcher.yml)
- **Доказательства кандидата** находятся по ссылке из Draft PR. Сверяйте
  SHA базы, run и попытку Prepare, раздел **Release readiness** с JSON и
  логи отдельных шагов. `passed`, `failed`, `skipped`, `not run`,
  `not applicable` и `unknown` различаются. Успех задания с пропусками
  подтверждает только выполненные проверки. Полная hosted-проверка
  кандидата и принятие пилота требуют отдельных подтверждений владельца.

## Полномочия и условия публикации

Релизный workflow начинает с `permissions: {}`. Prepare получает
`contents: read` и `pull-requests: read`, Publish — `contents: write`
и `pull-requests: write`. Оба выбирают `GITHUB_SHA` и сверяют чистый
checkout. Каждый checkout использует `persist-credentials: false`.
Discover получает встроенный токен через `GH_TOKEN`; шаг публикации
использует его для Git и запроса Draft PR. Аутентификация Git остаётся
в окружении команды. Доступ к секретам и checkout с `.git` в контейнер
Gentoo не передаётся. Он получает временную копию с правом записи и
проверенный `DISTDIR` только для чтения.

Задания ограничены 20 и 10 минутами соответственно. Группа
`noctalia-release-preparation` допускает один активный запуск и сохраняет
работающий запуск при новом запросе (`cancel-in-progress: false`).
Параллельные действия владельца и GitHub всё равно могут изменить ветки
и PR между проверками.

Publish требует точного, в том числе по регистру, контекста
`vovanbl411/noctalia-overlay`, `refs/heads/main` и
`vovanbl411/noctalia-overlay/.github/workflows/noctalia-release-watcher.yml@refs/heads/main`.
Разрешены `schedule` и `workflow_dispatch` при успешном Prepare,
полной записи готовности и выключенном dry-run. Checkout, `GITHUB_SHA`
и база handoff должны совпасть. И fetch, и push URL `origin` должны
точно совпадать с `https://github.com/vovanbl411/noctalia-overlay.git`.

### Допуск артефакта

Prepare загружает `release-handoff-<run_id>-<run_attempt>` на 30 дней.
Publish получает точный ID из записи готовности текущего запуска и попытки,
сверяет SHA-256 исходного ZIP до распаковки и допускает только ожидаемые
пути, типы и размеры файлов. До записи проверяются схема v2, источник и
ротация, совпадение ebuild кандидата с сохранённым шаблоном, Manifest,
сгенерированные блоки README, итоговый индекс и коммит. Изменения
ограничены добавлением нового ebuild, удалением старого fallback и
обновлением Manifest и двух README. [Состав и происхождение
handoff](AUTOMATION.md#handoff--передача-результата)

### Разрешённые записи

Publish может создать отсутствующую ветку `automation/noctalia-vX.Y.Z`
и открыть Draft PR в `main`. Перед push и перед запросом PR проверяются
открытые PR с конфликтующим заголовком, история PR точной ветки и
актуальность удалённой базы. Перед PR также сверяется head кандидата с
подтверждённым коммитом. Существующий ref, даже с тем же коммитом, блокирует
запись. Конфликтующий открытый PR, ранее закрытый PR точной ветки или
неоднозначная выдача API также блокируют запись. Слияние выполняет владелец.

## Диагностика и восстановление

Начните с первого невыполненного обязательного шага и его лога. Для
Publish прочитайте его сводку целиком, прежде чем решать, повторять ли
запуск. Ошибки CLI начинаются с `Release publication error:`.

| Ситуация | Что могло измениться и что проверить |
| --- | --- |
| Discover или ротация остановились | Ветки и PR не менялись. Проверьте исход Watcher, тег, `VERSION`, базу и политику двух версий. `up-to-date`, `already-reported` и dry-run могут штатно пропустить дальнейшие шаги. |
| Источник, Gentoo или сверка Manifest завершились ошибкой | Проверьте первый отказавший шаг, ожидаемый `SRC_URI`, SHA, отчёт архива, итог генерации Manifest и `pkgcheck`. Неудачная проверка оставляет кандидата без допуска. |
| Upload, readiness, checkout или preflight отклонили кандидата | Возможно, ZIP уже сохранён в Actions. Сверьте run, попытку, ID, digest и базу. Повтор только Publish со старой попыткой Prepare будет отклонён. При отсутствии удалённой записи повторяйте весь workflow от нужной базы. |
| Ветка или конфликтующий PR существовали до push, либо `main` ушёл вперёд | Publish останавливается до записи. Выясните принадлежность ветки и историю PR. Для устаревшей базы нужен новый Prepare на актуальном `main`. |
| Push отказал, результат неоднозначен или Git ответил `up-to-date` | Запрос Draft PR не выполняется. Сверьте повторно прочитанный ref и PR точной ветки. Наличие ref при чтении не подтверждает, что его создал этот запуск. |
| Создание ветки подтверждено, а повторная проверка или запрос PR завершились ошибкой | Ветка уже могла остаться на GitHub. Сверьте строку **Last confirmed write**, имя ref, коммит и **Exact-head PR state**. Пустое чтение после неоднозначного запроса PR оставляет его исход `UNKNOWN`. |

Отсутствующая ветка — наблюдение перед попыткой записи. Уже существующая
ветка требует решения владельца. Новый ref считается созданным этим
запуском только при ответе push `[new branch]`. Если другой участник
создаст ref между чтением и push, условие ожидаемого отсутствия защищает
ветку от замены.

После возможной записи владелец сверяет принадлежность ветки, точный head,
его родителя и дерево с проверенной базой и diff handoff. Затем проверяет
текущий удалённый `main` и всю историю PR по этой ветке. Если PR уже
есть, сначала проверяет его. Вручную открыть Draft PR можно для проверенной
ветки с актуальной базой после разрешения конфликтов. При отказе от
кандидата удалять можно только подтверждённую осиротевшую ветку, которой
владеет сопровождающий. Удаление сохраняет историю PR. Конфликт с ранее
закрытым PR решается отдельно. Автоматизация сохраняет create-only
политику, а повторная запись, удаление, перезапись и переиспользование
ветки остаются за пределами её полномочий.

## Приёмка владельцем

1. Сверьте точный head PR, целевую ветку `main`, родителя коммита и diff
   ebuild, Manifest и README. Проверьте upstream release notes,
   происхождение, необычные сведения о подписи, упаковку и зависимости.
2. Из PR откройте указанную попытку Prepare. Сверьте SHA базы, readiness,
   результаты применимых проверок и `Python unit tests` на точном head.
3. На Gentoo проверьте, что настроенный оверлей указывает на рабочую
   копию кандидата, а `git rev-parse HEAD` там совпадает с head PR.
   `doas emerge -pv gui-apps/noctalia` показывает план и выбор репозитория.
   Сверьте версию кандидата и `::noctalia-overlay` до установки. Запишите
   head, версию, профиль, архитектуру, USE и способ установки.
4. Установите точного кандидата из этой копии. Установка из binpkg
   подтверждает установку бинарного пакета. Для доказательства сборки из
   исходников зафиксируйте такую сборку отдельно. Проверьте в обычной
   сессии Niri панель, уведомления, сеть, звук и блокировку. Запишите
   результаты и слейте PR вручную после прохождения применимых проверок.

## Ожидаемые настройки GitHub

Эти параметры описывают целевую конфигурацию. Владелец подтверждает фактические настройки и унаследованные
ограничения.

**Rulesets** в `Settings → Rules → Rulesets`.

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

Ноль обязательных одобрений выбран для персонального репозитория. Защиту
обеспечивают PR, требуемый CI и ручное решение владельца. `Restrict updates`
с пустым списком обхода может заблокировать обычное слияние PR.

**Actions** в `Settings → Actions → General`.

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

Allowlist и закрепление по SHA действуют отдельно. В workflow Actions
закреплены на полные SHA, образы Gentoo — на SHA-256 digest. Digests
обновляют вручную. [Dependabot](../.github/dependabot.yml) обновляет только
GitHub Actions, его PR проходят ревью, CI и ручное слияние. Публикация
использует встроенный `GITHUB_TOKEN`. PAT и приватные SSH-ключи в
репозитории отсутствуют.

При проверке effective settings учитывайте rulesets организации и
репозитория, legacy branch protection, дополнительные checks, права
обхода и слияния, allowlist, закрепление по SHA, права токена, сроки
хранения, создание Draft PR и отдельно одобрение запуска его CI.
На разрешённом проверочном кандидате подтвердите появление нужного check
на его head и блокировку merge при отсутствии или сбое check. Состояние
Secret Scanning и Push Protection также проверяется в GitHub. Их
рекомендуется включить.

## Исторические наблюдения

- **Запуск `45663950280`.** В [запуске по расписанию](https://github.com/vovanbl411/noctalia-overlay/actions/runs/45663950280)
  на `cee4a4fb99cf08a39df1d8aa6613d8a8cf397b2c` Prepare завершился
  успешно. По данным jobs API, Publish и шаги `Create sanitized overlay workspace`,
  `Prepare the release rotation`, `Generate Manifest and run pkgcheck`,
  `Create release handoff`, `Upload release handoff` и
  `Mark release handoff ready` получили `SKIPPED`. Доказательство относится
  к указанному запуску и пропускам. Этот запуск не подтверждает полный
  hosted-прогон кандидата на проверяемой реализации.
- **Защита `main` на 2026-11-25 UTC.** Публичные [Protect main](https://github.com/vovanbl411/noctalia-overlay/rules/23505842)
  и [правила ветки](https://api.github.com/repos/vovanbl411/noctalia-overlay/rules/branches/main)
  показывали активный ruleset с PR, разрешением обсуждений, актуальной базой
  и `Python unit tests` (интеграция `15368`), запретом удаления и force
  push и нулём обязательных одобрений. Bypass и legacy protection
  оставались неподтверждёнными.
- **Уведомления на одной станции.** Noctalia 5.2.1 обслуживала
  `org.freedesktop.Notifications`, включая уведомления Thunderbird. На
  2026-10-07 [ebuild Gentoo virtual/notification-daemon-0](https://github.com/gentoo/gentoo/blob/master/virtual/notification-daemon/notification-daemon-0.ebuild)
  ещё не перечислял Noctalia. В той конфигурации `x11-libs/libnotify` через
  `PDEPEND="virtual/notification-daemon"` мог выбрать X11-демон с
  `gtk+[X]`, `cairo[X]` и `libXcursor`. На станции `x11-libs/libnotify`
  хранился в world явно: Gentoo ebuild Thunderbird предлагал его через
  `optfeature "desktop notifications"`, без обязательного `RDEPEND`.
  Собственного ebuild Thunderbird для этой зависимости не было.
  [Исходная запись](https://github.com/vovanbl411/noctalia-overlay/blob/14b374edae214d15876f27c60b246d48f193a57c/README.md#L94-L130)
