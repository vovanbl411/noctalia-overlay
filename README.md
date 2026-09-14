# noctalia-overlay

Личный Gentoo-оверлей с пакетами `gui-apps/noctalia`. Он нужен, чтобы
оперативно сопровождать стабильные релизы Noctalia, не дожидаясь обновления в
GURU. В оверлее намеренно нет live-версии `9999`.

## Содержимое

| Пакет | Назначение |
| --- | --- |
| `gui-apps/noctalia-5.1.0` | Текущий стабильный релиз |
| `gui-apps/noctalia-5.0.1` | Предыдущая версия для отката |

Оверлей наследует eclass'ы из основного репозитория Gentoo. Для Noctalia
сохраняется `KEYWORDS="~amd64"`, поэтому действующая запись
`gui-apps/noctalia ~amd64` в `package.accept_keywords` остаётся нужна.

## Подключение

Файл: `/etc/portage/repos.conf/noctalia-overlay.conf`

```ini
[noctalia-overlay]
location = /var/db/repos/noctalia-overlay
sync-type = git
sync-uri = https://github.com/vovanbl411/noctalia-overlay.git
auto-sync = yes
priority = 50
```

Клонировать оверлей при первом подключении:

```bash
doas git clone https://github.com/vovanbl411/noctalia-overlay.git \
  /var/db/repos/noctalia-overlay
```

После подключения проверь, что выбран пакет из личного оверлея, а не GURU:

```bash
doas emaint sync -r noctalia-overlay
doas emerge -pv =gui-apps/noctalia-5.1.0
```

Оверлей имеет более высокий приоритет, чем GURU, но GURU не нужно отключать:
он остаётся источником других пакетов, например Waydroid.

## Обновление Noctalia

Перед добавлением новой версии:

1. Выбери только стабильный тег формата `vX.Y.Z`; prerelease и `main` не
   используются.
2. Проверь release notes, `PACKAGING.md` и подпись тега в репозитории upstream.
3. Добавь новый ebuild и сгенерируй Manifest из полученного архива.
4. Запусти `pkgcheck scan`, затем `doas emerge -pv` для новой версии.
5. Обнови Noctalia вручную и проверь запуск сессии Niri. Старый ebuild оставь
   хотя бы до успешной проверки, чтобы откат оставался простым.

Автоматизация пока не изменяет ebuild'ы и не обновляет установленный пакет.
Её первый этап будет только создавать GitHub Issue о новом стабильном релизе.

## Проверка после обновления

```bash
noctalia --version
```

Проверь также панель, уведомления, сеть, звук и блокировку экрана в обычной
сессии Niri.
