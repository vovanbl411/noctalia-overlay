# noctalia-overlay

[English](README.en.md)

Он нужен, чтобы
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
| `gui-apps/noctalia-5.2.1` | Текущий стабильный релиз |
| `gui-apps/noctalia-5.2.0` | Предыдущая версия для отката |
<!-- noctalia-versions:end -->

Оверлей наследует eclass'ы из основного репозитория Gentoo. Для Noctalia
сохраняется `KEYWORDS="~amd64"`, действующая запись `gui-apps/noctalia ~amd64` в `package.accept_keywords` остаётся нужна.

Подробная схема CI, branch protection и repository security settings описана в
[`docs/CI_AND_SECURITY.md`](docs/CI_AND_SECURITY.md).

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

## Интеграция уведомлений с Portage

На reference workstation Noctalia 5.2.1 предоставляет D-Bus service
`org.freedesktop.Notifications`; внешние уведомления, включая уведомления
Thunderbird, работают. Проверенный 2026-10-07
[`::gentoo virtual/notification-daemon-0`](https://github.com/gentoo/gentoo/blob/master/virtual/notification-daemon/notification-daemon-0.ebuild)
пока не знает Noctalia как provider. Поэтому `x11-libs/libnotify` с
`PDEPEND="virtual/notification-daemon"` выбирает другой notification daemon.
Стандартный fallback на `x11-misc/notification-daemon` тянет X11 stack через
`gtk+[X]`, `cairo[X]` и `libXcursor`, что нежелательно для Wayland-only системы.

Локальный `virtual/notification-daemon-0-r1` сохраняет upstream providers,
KEYWORDS и семантику USE-флагов `gnome`/`kde`, добавляя `gui-apps/noctalia` в
fallback OR-group при `!gnome` и `!kde`. Ревизия `0-r1` новее upstream `0`:
при установленной Noctalia Portage может удовлетворить dependency без второго
daemon и без `package.provided`. Этот virtual сопровождается вручную и не
участвует в rotation двух версий Noctalia.

После синхронизации оверлея и перехода на local virtual временная запись
`virtual/notification-daemon-0` в `/etc/portage/profile/package.provided`
больше не нужна. Удалите только эту запись и проверьте resolver:

```bash
emerge -pv virtual/notification-daemon
```

При `USE="-gnome -kde"` и установленной Noctalia должен выбираться
`virtual/notification-daemon-0-r1::noctalia-overlay` без
`x11-misc/notification-daemon` и без требования включить `USE=X` для `gtk+`
или `cairo`.

На reference workstation `x11-libs/libnotify` остаётся explicit world package:
текущий Gentoo ebuild Thunderbird лишь предлагает его через
`optfeature "desktop notifications"`, а обязательного `RDEPEND` на него нет.
Собственный Thunderbird ebuild ради этой dependency не сопровождается.

## Проверка release PR

Перед merge Draft PR:

1. Убедиться, что опубликован stable release с тегом `vX.Y.Z`; prerelease и
   `main` не используются. Automation разрешает release tag в exact commit SHA
   и проверяет, что `VERSION` в этом commit совпадает с версией release.
2. Проверить release notes, provenance/signing identity при необычных признаках
   и изменения `PACKAGING.md` у upstream.
3. Проверить сгенерированные ebuild и Manifest, результат `pkgcheck scan` и
   выполнить `doas emerge -pv gui-apps/noctalia`.
4. Установить candidate из ветки PR и проверить обычную Niri-сессию.
5. Проверить панель, уведомления, сеть, звук и блокировку экрана. Только после
   успешной проверки вручную merge Draft PR.

## Автоматизация релизов

Workflow
[Prepare Noctalia release](.github/workflows/noctalia-release-watcher.yml)
запускается ежедневно в 06:17 UTC и вручную через `workflow_dispatch`.
Процесс выглядит так:

```text
stable GitHub Release
      ↓
release tag → exact commit SHA → VERSION match
      ↓
packaging inspection / preparation
      ↓
Draft PR
      ↓
manual review + Niri test
      ↓
merge
      ↓
candidate becomes current, previous current becomes fallback
```

Подпись Git tag, если она есть, сохраняется как дополнительная provenance
metadata. Она не является обязательным условием принятия stable release.

[`scripts/watch_noctalia_release.py`](scripts/watch_noctalia_release.py)
запрашивает latest release через GitHub API, принимает только строгие теги
`vX.Y.Z`, проверяет policy двух версий и ищет уже открытый PR для этого тега.
Поэтому повторный scheduled run не создаёт duplicate PR. Для нового релиза
[`scripts/prepare_noctalia_release.py`](scripts/prepare_noctalia_release.py)
создаёт candidate ebuild копированием current, удаляет прежний fallback и
обновляет таблицу версий выше. После этого workflow в закреплённом Gentoo
container пересоздаёт Manifest, запускает `pkgcheck scan --exit` и открывает Draft PR.
В PR отдельно показано, менялись ли upstream `PACKAGING.md`, `meson.build` и
`meson_options.txt`.

Параметр `dry_run` выполняет discovery, policy и расчёт будущей rotation,
записывает summary, но не изменяет workspace, не создаёт branch, commit или PR.
Read-only job `prepare` не имеет write permissions. Write permissions
`contents: write` и `pull-requests: write` получает только `publish` после
успешной подготовки минимального release artifact. Он не выполняет auto-merge
и не устанавливает пакет на пользовательскую машину. GitHub может задержать
scheduled run; в публичном репозитории расписание отключается после 60 дней
без активности. В обоих случаях workflow можно запустить вручную.

Чтобы workflow мог создавать Draft PR, владелец repository должен включить
`Settings → Actions → General → Workflow permissions → Allow GitHub Actions to create and approve pull requests`.
Этот параметр workflow не изменяет.

Gentoo Docker images закреплены по SHA256 digest. Dependabot обновляет только
GitHub Actions, поэтому эти digest нужно периодически проверять и обновлять
вручную.

Скрипты используют только стандартную библиотеку Python. Unit-тесты в
`tests/` не требуют доступа к GitHub. Общий workflow
[CI](.github/workflows/ci.yml) запускает только Python unit tests.
Пересоздание Manifest и `pkgcheck scan --exit` выполняются в release workflow
при подготовке Noctalia release PR. Ручные изменения
`virtual/notification-daemon/` проверяются локально командами `pkgcheck scan`
и `emerge -pv virtual/notification-daemon`; зелёный общий CI не подтверждает
эти проверки.

## Проверка после обновления

```bash
noctalia --version
```

Проверить также панель, уведомления, сеть, звук и блокировку экрана в обычной
сессии Niri.
