# noctalia-overlay

Личный Gentoo-оверлей с пакетами `gui-apps/noctalia`. Он нужен, чтобы
сопровождать стабильные релизы Noctalia, не дожидаясь обновления в
GURU. В оверлее намеренно нет live-версии `9999`.

В `main` всегда поддерживаются ровно две стабильные версии Noctalia: текущая
и предыдущая для быстрого rollback. Rotation выполняется только атомарно в
release PR: новая версия добавляется, текущая становится fallback, а прежний
fallback удаляется. До merge `main` не меняется.

## Содержимое

<!-- noctalia-versions:start -->
| Пакет | Назначение |
| --- | --- |
| `gui-apps/noctalia-5.1.0` | Текущий стабильный релиз |
| `gui-apps/noctalia-5.0.1` | Предыдущая версия для отката |
<!-- noctalia-versions:end -->

Оверлей наследует eclass'ы из основного репозитория Gentoo. Для Noctalia
сохраняется `KEYWORDS="~amd64"`, действующая запись `gui-apps/noctalia ~amd64` в `package.accept_keywords` остаётся нужна.

## Подключение и синхронизация

Файл: `/etc/portage/repos.conf/noctalia-overlay.conf`

```ini
[noctalia-overlay]
location = /var/db/repos/noctalia-overlay
sync-type = git
sync-uri = https://github.com/vovanbl411/noctalia-overlay.git
auto-sync = yes
priority = 50
```

После сохранения конфигурации выполнить первую синхронизацию:

```bash
doas emaint sync -r noctalia-overlay
```

Portage сам клонирует репозиторий в
`/var/db/repos/noctalia-overlay`, если каталог ещё не существует. При
следующих вызовах та же команда получает изменения из Git.

Проверить, что выбран пакет из данного оверлея, а не GURU:

```bash
doas emerge -pv gui-apps/noctalia
```

Оверлей имеет более высокий приоритет, чем GURU, но GURU не стоит отключать:
он остаётся источником других пакетов.

`auto-sync = yes` включает оверлей в общий `doas emerge --sync` и
`doas emaint sync --auto`. Эта опция не создаёт фоновый таймер: синхронизация
происходит только при явном запуске одной из этих команд.

## Проверка release PR

Перед merge Draft PR:

1. Убедиться, что release имеет стабильный тег `vX.Y.Z`; prerelease и `main` не
   используются.
2. Проверить release notes, подпись тега и изменения `PACKAGING.md` у upstream.
3. Проверить сгенерированные ebuild и Manifest, результат `pkgcheck scan` и
   выполнить `doas emerge -pv gui-apps/noctalia`.
4. Установить candidate из ветки PR и проверить обычную Niri-сессию.
5. Проверить панель, уведомления, сеть, звук и блокировку экрана. Только после
   успешной проверки вручную merge Draft PR.

## Автоматизация релизов

Workflow
[Prepare Noctalia release](.github/workflows/noctalia-release-watcher.yml)
запускается ежедневно в 06:00 UTC и вручную через `workflow_dispatch`.
Процесс выглядит так:

```text
upstream release
      ↓
scheduled watcher
      ↓
Draft PR
      ↓
automated checks
      ↓
manual review + Niri test
      ↓
merge
      ↓
candidate becomes current, previous current becomes fallback
```

[`scripts/watch_noctalia_release.py`](scripts/watch_noctalia_release.py)
запрашивает latest release через GitHub API, принимает только строгие теги
`vX.Y.Z`, проверяет policy двух версий и ищет уже открытый PR для этого тега.
Поэтому повторный scheduled run не создаёт duplicate PR. Для нового релиза
[`scripts/prepare_noctalia_release.py`](scripts/prepare_noctalia_release.py)
создаёт candidate ebuild копированием current, удаляет прежний fallback и
обновляет таблицу версий выше. После этого workflow в закреплённом Gentoo
container пересоздаёт Manifest, запускает `pkgcheck scan` и открывает Draft PR.
В PR отдельно показано, менялись ли upstream `PACKAGING.md`, `meson.build` и
`meson_options.txt`.

Параметр `dry_run` выполняет discovery, policy и расчёт будущей rotation,
записывает summary, но не изменяет workspace, не создаёт branch, commit или PR.
Workflow использует встроенный `GITHUB_TOKEN` только с `contents: write` и
`pull-requests: write`. Он не выполняет auto-merge и не устанавливает пакет на
пользовательскую машину. GitHub может задержать scheduled run; в публичном
репозитории расписание отключается после 60 дней без активности. В обоих
случаях workflow можно запустить вручную.

Скрипты используют только стандартную библиотеку Python. Unit-тесты в
`tests/` не требуют доступа к GitHub; `pkgcheck` и Manifest проверяются в CI.

## Проверка после обновления

```bash
noctalia --version
```

Проверить также панель, уведомления, сеть, звук и блокировку экрана в обычной
сессии Niri.
