# noctalia-overlay

Личный Gentoo-оверлей с пакетами `gui-apps/noctalia`. Он нужен, чтобы 
сопровождать стабильные релизы Noctalia, не дожидаясь обновления в
GURU. В оверлее намеренно нет live-версии `9999`.

## Содержимое

| Пакет | Назначение |
| --- | --- |
| `gui-apps/noctalia-5.1.0` | Текущий стабильный релиз |
| `gui-apps/noctalia-5.0.1` | Предыдущая версия для отката |

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

## Обновление Noctalia

Перед добавлением новой версии:

1. Выбрать только стабильный тег формата `vX.Y.Z`; prerelease и `main` не
   используются.
2. Проверить release notes, `PACKAGING.md` и подпись тега в репозитории upstream.
3. Добавить новый ebuild и сгенерировать Manifest из полученного архива.
4. Запустить `pkgcheck scan`, затем `doas emerge -pv` для новой версии.
5. Обновить Noctalia вручную и проверь запуск сессии Niri. Старый ebuild оставить
   хотя бы до успешной проверки, чтобы откат оставался простым.

## Автоматизация релизов

Workflow
[Watch Noctalia releases](.github/workflows/noctalia-release-watcher.yml)
запускается ежедневно в 06:00 UTC и вручную через `workflow_dispatch`.
Он запускает
[`scripts/watch_noctalia_release.py`](scripts/watch_noctalia_release.py).
Скрипт получает последний релиз upstream через GitHub API, принимает только
теги формата `vX.Y.Z` и сравнивает их с ebuild'ами в этом репозитории.

Если стабильная версия новее, workflow создаёт один Issue с чек-листом для
ручной подготовки обновления. Перед созданием он ищет Issue с тем же тегом
среди открытых и закрытых задач, поэтому повторных уведомлений не будет.
Параметр `dry_run` при ручном запуске выводит результат, но не создаёт Issue.

Workflow использует встроенный `GITHUB_TOKEN` с правами только на чтение
содержимого и создание Issue. Он не меняет ebuild'ы, Manifest или установленный
пакет. GitHub может задержать запланированный запуск; в публичном репозитории
расписание отключается после 60 дней без активности. В обоих случаях watcher
можно запустить вручную.

Скрипт использует только стандартную библиотеку Python. Проверки его решений
лежат в `tests/test_watch_noctalia_release.py` и не требуют доступа к GitHub.

## Проверка после обновления

```bash
noctalia --version
```

Проверить также панель, уведомления, сеть, звук и блокировку экрана в обычной
сессии Niri.
