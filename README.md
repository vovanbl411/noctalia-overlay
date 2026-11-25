# noctalia-overlay

[English](README.en.md)

Оверлей позволяет сопровождать стабильные релизы Noctalia до их появления
в GURU. Live-версии `9999` в оверлее нет.

В `main` хранятся ровно две стабильные версии Noctalia, текущая и предыдущая
для отката. В одном release PR добавляется кандидат, текущая версия остаётся
резервной, прежняя резервная удаляется, а таблицы версий в обоих README
обновляются. Владелец принимает ротацию, когда вручную сливает PR.

## Содержимое

<!-- noctalia-versions:start -->
| Пакет | Назначение |
| --- | --- |
| `gui-apps/noctalia-5.6.0` | Текущий стабильный релиз |
| `gui-apps/noctalia-5.5.0` | Предыдущая версия для отката |
<!-- noctalia-versions:end -->

Оверлей наследует eclass'ы из основного репозитория Gentoo и использует его
профили. У ebuild Noctalia указан только `KEYWORDS="~amd64"`. Оставьте запись
`gui-apps/noctalia ~amd64` в `package.accept_keywords`. У оверлея нет отдельного
профиля Gentoo или результатов проверки по матрице других профилей и архитектур.
Noctalia работает под Wayland. Ручная проверка релиза выполняется в Niri.

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

## Установка и откат Noctalia

Синхронизируйте оверлей, разрешите keyword и установите пакет.

```bash
doas emerge --ask gui-apps/noctalia::noctalia-overlay
```

У ebuild один собственный USE-флаг, `+jemalloc`. По умолчанию он включён,
добавляет зависимость `dev-libs/jemalloc:=` и включает соответствующую функцию
Meson. Чтобы отключить и зависимость, и функцию, укажите `-jemalloc` в
`package.use`. Ebuild также предлагает дополнительные пакеты для управления
яркостью (`app-misc/ddcutil`), батареей и устройствами питания
(`sys-power/upower`), автоматической вставки из буфера (`gui-apps/wtype`) и
аппаратно ускоренной записи экрана (`media-video/gpu-screen-recorder`).

Для отката укажите точную резервную версию из таблицы выше. Сначала проверьте
план отката, затем установите пакет. В командах ниже пример для `5.5.0`.

```bash
doas emerge -pv '=gui-apps/noctalia-5.5.0::noctalia-overlay'
doas emerge --ask --oneshot '=gui-apps/noctalia-5.5.0::noctalia-overlay'
```

После смены версии проверьте `noctalia --version` и сценарии Niri ниже.
Следующее обновление world может снова выбрать более новую версию.

## Интеграция уведомлений с Portage

На тестовой рабочей станции Noctalia 5.2.1 предоставляла D-Bus-сервис
`org.freedesktop.Notifications` и показывала внешние уведомления, в том числе
из Thunderbird. Это наблюдение относится к указанным версии и машине.

На 2026-10-07 ebuild пакета
[`::gentoo virtual/notification-daemon-0`](https://github.com/gentoo/gentoo/blob/master/virtual/notification-daemon/notification-daemon-0.ebuild)
не указывал Noctalia среди провайдеров. В той конфигурации `x11-libs/libnotify` с
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

Перед слиянием выполните [проверки приёмки](docs/CI_AND_SECURITY.md#приёмка-владельцем).
Сверьте точный коммит PR и базу `main`, изменения ebuild и Manifest, изменения
и происхождение upstream-релиза, отчёт Prepare по ссылке из PR и обязательную
проверку CI. Выполните `doas emerge -pv gui-apps/noctalia`, установите кандидата
из ветки PR и проверьте обычную сессию Niri. Перед ручным слиянием проверьте
панель, уведомления, сеть, звук и блокировку экрана.

## Автоматизация релизов

[Prepare Noctalia release](.github/workflows/noctalia-release-watcher.yml)
запускается ежедневно в 06:17 UTC или вручную через `workflow_dispatch`.
GitHub может задержать запуск по расписанию или отключить расписание
в публичном репозитории после 60 дней без активности. В этих случаях
доступен ручной запуск.

[`scripts/watch_noctalia_release.py`](scripts/watch_noctalia_release.py)
проверяет последний стабильный релиз на GitHub, определяет коммит по тегу,
сверяет `VERSION` в этом коммите и ищет открытые PR для релиза. Подпись тега,
если она есть, дополняет сведения о происхождении. Допускаются и релизы без
подписи. [`scripts/prepare_noctalia_release.py`](scripts/prepare_noctalia_release.py)
копирует текущий ebuild, удаляет прежний резервный и обновляет обе таблицы
версий в изолированной рабочей копии. Workflow проверяет канонический архив
тега, создаёт и сверяет Manifest и запускает `pkgcheck scan --exit` в Gentoo.
В Draft PR перечислены изменения upstream-файлов `PACKAGING.md`, `meson.build`
и `meson_options.txt`. Там же указаны ссылка на запуск workflow и номер
проверенной попытки Prepare.

При `dry_run=true` workflow выполняет поиск релиза, проверяет политику,
показывает план ротации и запускает unit-тесты. Проверки архива и Gentoo,
а также публикация пропускаются. Только задание Publish получает права записи
в репозиторий. Оно проверяет handoff и удалённую базу, создаёт ветку кандидата
при её отсутствии и открывает Draft PR для ручной приёмки. Проверки источника,
права, повторные запуски и восстановление описаны в
[руководстве по CI и безопасности](docs/CI_AND_SECURITY.md).

Образы Gentoo закреплены по SHA-256 и обновляются вручную. Dependabot обновляет
только GitHub Actions.

## Локальные тесты

Запускайте из корня рабочей копии оверлея с Python 3 и Git в `PATH`. Тесты
используют стандартную библиотеку Python и временные локальные
Git-репозитории. Для них не нужны сеть и Portage.

```bash
python3 -m unittest discover -s tests
```

[CI](.github/workflows/ci.yml) запускает этот набор unit-тестов. Для ручных
изменений `virtual/notification-daemon/` выполните в Gentoo `pkgcheck scan` и
`emerge -pv virtual/notification-daemon`. Эти проверки выполняются отдельно
от общего CI и ротации Noctalia.

## Проверка после обновления

```bash
noctalia --version
```

Проверить также панель, уведомления, сеть, звук и блокировку экрана в обычной
сессии Niri.
