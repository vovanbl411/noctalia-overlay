# noctalia-overlay

[English](README.en.md)

Оверлей для Gentoo позволяет устанавливать стабильные релизы Noctalia до их
появления в GURU. Noctalia — оболочка для Wayland. Для ручной проверки
релизов в этом оверлее используют Niri.

## Пакеты

<!-- noctalia-versions:start -->
| Пакет | Назначение |
| --- | --- |
| `gui-apps/noctalia-5.6.0` | Текущий стабильный релиз |
| `gui-apps/noctalia-5.5.0` | Предыдущая версия для отката |
<!-- noctalia-versions:end -->

В `main` хранятся ровно два стабильных ebuild Noctalia. Live-версии
`9999` нет. Отдельный пакет `virtual/notification-daemon` позволяет
Portage учитывать установленную Noctalia как поставщика уведомлений
при определённых USE-флагах.

## Подключение

Создайте файл `/etc/portage/repos.conf/noctalia-overlay.conf` со следующим
содержимым.

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

`-r` выбирает этот репозиторий. При первом запуске Portage клонирует его в
указанный каталог, при следующих — обновляет локальную копию. С
`auto-sync = yes` оверлей также обновляется при явном запуске
`doas emerge --sync` или `doas emaint sync --auto`. Приоритет `50` обычно
ставит оверлей выше GURU. Оставьте GURU подключённым для других пакетов.

## Установка и USE-флаги

Оверлей использует eclass и профили основного репозитория Gentoo.
Noctalia имеет только keyword `~amd64`. Добавьте
`gui-apps/noctalia ~amd64` в `package.accept_keywords`.

```bash
doas emerge -pv gui-apps/noctalia
doas emerge --ask gui-apps/noctalia::noctalia-overlay
```

Первая команда показывает план установки и зависимости. В `-pv` флаг
`-p` включает предварительный просмотр, а `-v` показывает подробности.
Убедитесь, что Portage выбрал `::noctalia-overlay`. Во второй команде
этот суффикс задаёт источник пакета, а `--ask` запрашивает
подтверждение перед установкой.

USE-флаг `+jemalloc` включён по умолчанию. Он добавляет зависимость
`dev-libs/jemalloc:=` и включает соответствующую функцию в Meson.
Запись `gui-apps/noctalia -jemalloc` в `package.use` отключает обе части.
В [ebuild](gui-apps/noctalia/) также перечислены необязательные инструменты
для яркости (`app-misc/ddcutil`), питания (`sys-power/upower`),
автовставки из буфера (`gui-apps/wtype`) и аппаратной записи экрана
(`media-video/gpu-screen-recorder`).

## Уведомления

Локальный [virtual](virtual/notification-daemon/) сохраняет поставщиков,
KEYWORDS и правила Gentoo для USE-флагов `gnome` и `kde`. При выключенных
`gnome` и `kde` в список допустимых поставщиков добавляется
`gui-apps/noctalia`. Установленная Noctalia может закрыть зависимость без
установки второго демона и без записи в `package.provided`. Этот пакет
сопровождается вручную.

```bash
emerge -pv virtual/notification-daemon
```

При `USE="-gnome -kde"` и установленной Noctalia ожидайте в плане
`virtual/notification-daemon-0-r1::noctalia-overlay` без установки
`x11-misc/notification-daemon` и без требований `USE=X` для `gtk+` и
`cairo`. Если при переходе на этот virtual сохранилась временная запись
`virtual/notification-daemon-0` в `/etc/portage/profile/package.provided`,
удалите только её и повторите предварительную проверку. [Историческое
наблюдение о D-Bus и Thunderbird](docs/CI_AND_SECURITY.md#исторические-наблюдения)

## Обновление и откат

Для обновления повторите синхронизацию и команды установки.
Для отката подставьте версию из строки «Предыдущая версия для отката» в
таблице пакетов.

```bash
doas emerge -pv '=gui-apps/noctalia-<fallback-version>::noctalia-overlay'
doas emerge --ask --oneshot '=gui-apps/noctalia-<fallback-version>::noctalia-overlay'
```

`=` фиксирует версию. Сначала проверьте план, затем подтвердите установку.
`--oneshot` оставляет набор world без изменений. Следующее обновление world
может снова выбрать текущую версию.

После обновления или отката проверьте `noctalia --version`, затем панель,
уведомления, сеть, звук и блокировку экрана в обычной сессии Niri.

## Проверки и сопровождение

CI репозитория запускает unit-тесты автоматизации. При полном прогоне
подготовки кандидата проверяются архив, Manifest и метаданные Gentoo.
Проверки зависимостей, сборки и работы сеанса выполняют отдельно.
Оверлей не заявляет матрицу проверок для других профилей и архитектур.
Доказательства для конкретного кандидата ищите в его PR и отчёте Prepare.

Автоматизация готовит ротацию и открывает Draft PR для нового стабильного
релиза. Владелец проверяет кандидата и принимает обновление ручным слиянием.
Сопровождающим доступны [пошаговый разбор автоматизации](docs/AUTOMATION.md)
и [справочник CI, безопасности и восстановления](docs/CI_AND_SECURITY.md).
