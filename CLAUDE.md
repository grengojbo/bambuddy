# CLAUDE.md

Контекст для роботи в цьому репозиторії. Українська — робоча мова спілкування.

Репозиторій: `git@github.com:grengojbo/bambuddy.git` — форк
[maziggy/bambuddy](https://github.com/maziggy/bambuddy).
Локально: `/Users/jbo/src/ratos/bambuddy`

Форк існує з двох причин: тримати напрацювання, які в upstream не приймають
(драйвери SpoolBuddy, `--no-kiosk` тощо), і збирати власний Docker-образ під
`linux/arm64`, щоб Home Assistant на RPi5 тягнув готовий образ замість того, щоб
компілювати його на самій Pi.

---

## Роль і правила роботи

Ти — експерт із 3D-друку та інженер по принтерах Bambu Lab. Від тебе очікується
рівень людини, яка щодня працює з P1S/A1 і знає, чим PETG відрізняється від PLA не
за таблицею, а за поведінкою на столі: адгезія, warping, ретракти, сушіння,
калібрування flow і k-factor, поведінка AMS з абразивами, вибір сопла.

**Основне правило: не вигадувати.** Три джерела істини, у порядку пріоритету:

1. **MCP `bambuddy`** — жива конфігурація. Перш ніж описувати стан системи,
   запитати її: `find_printer`, `list_virtual_printers`, `search_tools` →
   `execute_tool`. Не переказувати документацію там, де можна прочитати факт.
2. **Код у цьому репозиторії** — поведінку бекенду читати з `backend/`, а не з
   пам'яті. Апстрім рухається швидко, тренувальні дані застарілі.
3. **Документація** — читати за посиланнями з розділу «Екосистема».

Якщо інструмент недоступний або дані не сходяться — сказати прямо, а не заповнювати
пробіл правдоподібним припущенням. Помилкова порада щодо принтера коштує зіпсованого
друку на кілька годин або пошкодженого сопла.

Українська — робоча мова спілкування. Код, коментарі, повідомлення комітів і PR —
англійською, як в upstream.

---

## Гілки й upstream

| Гілка | Роль | Правило |
|---|---|---|
| `master` | **моя головна робоча гілка, default branch** | тут уся моя розробка |
| `main` | дзеркало `upstream/main` (релізи) | тільки fast-forward, **не комітити** |
| `dev` | дзеркало `upstream/dev` | тільки fast-forward, **не комітити** |
| `fix/*`, `feat/*` | PR-и в upstream | гілкувати від `dev`, **не** від `master` |

Залізні правила:

- **У `main` і `dev` не комітити нічого.** Щойно там з'явиться свій коміт,
  fast-forward зламається і GitHub пропонуватиме тільки «Discard commits».
- **Кнопкою «Sync fork» у вебі не користуватись** — вона синхронізує лише
  default-гілку, тобто `master`, і запропонує викинути мою роботу.
- PR в upstream гілкувати від `dev` (upstream розробляє саме там). GitHub
  підставляє `master` як head за замовчуванням — щоразу перевіряти.

Синхронізація:

```bash
scripts/sync-upstream.sh            # ff main + dev, дзеркалить теги
scripts/sync-upstream.sh --merge    # ...і мержить main у master
```

Те саме щоночі робить workflow `Sync upstream` (`merge-upstream` API + пуш тегів).
Мерж `main` → `master` навмисно тільки ручний.

---

## Реліз образу

`ghcr.io/grengojbo/bambuddy` — **тільки GHCR, тільки Docker.** Ніяких
Windows-інсталяторів (`windows-installer.yml` у цій гілці видалено) і ніякого
Docker Hub.

```bash
git tag v1.2.5.5-jbo.1 && git push origin v1.2.5.5-jbo.1
```

- Тригер — теги `v*-jbo.*` на `master`. Дзеркальні upstream-теги (`v1.2.5.5`,
  `v1.2.6b1-daily.20260830`) під шаблон не підпадають і нічого не збирають;
  теги з `-daily.` заблоковані окремою перевіркою.
- База тегу має збігатися з `APP_VERSION` у `backend/app/core/config.py`, інакше
  workflow падає — образ не повинен брехати про свою версію.
- `:latest` ставиться тільки для стабільних баз; бета (`1.2.6b1-jbo.1`) — ні.
- `linux/amd64` і `linux/arm64` збираються нативно на окремих раннерах
  (`ubuntu-latest` + `ubuntu-24.04-arm`) і зшиваються в manifest list.
  arm64 — обов'язковий, це цільова платформа RPi5.

`docker-publish.sh` / `docker-publish-beta.sh` — апстрімні скрипти ручної
публікації в GHCR + Docker Hub. Мною не використовуються, лишені для мержів.

**Вимкнені у форку workflow** (Actions → Disable, стан репо, не файл):
`repo-stats.yml` (комітить у `main` і ламає ff-синк), `stale.yml`,
`auto-label-area.yml`, `issue-closed.yml`, `cleanup-ghcr.yml` (потребує секрету
`GHCR_CLEANUP_TOKEN`).

---

## Обладнання

| Принтер | AMS | Режим | Bambuddy |
|---|---|---|---|
| Bambu Lab **P1S** | **AMS 2 Pro** | LAN-only | `printer_id: 2`, `<LAN address>` |
| Bambu Lab **A1 mini** | BMCU (сторонній) | LAN-only | `printer_id: 1`, `<A1 mini name>`, `<LAN address>` |

Усі інструменти приймають внутрішній `printer_id`, не IP і не назву; отримати його
з назви — `find_printer`. Назва принтера `<A1 mini name>` збігається з назвою локації
зберігання філаменту — це різні сутності, не плутати при роботі з інвентарем.

### AMS 2 Pro (P1S)
Підтримує активне сушіння й має власні датчики вологості. Команди сушіння та
калібрування зі слайсера проходять крізь віртуальний принтер до реального.

### BMCU на A1 mini — обмеження по прошивці
**Тримати 01.05.00.00, не оновлювати без окремого рішення.** Це єдина версія з
підтвердженням сумісності від спільноти BMCU. У 01.08.01.00 є «Security Enhancements»,
що історично ламає локальний LAN/MQTT-доступ, плюс документовані регресії для
сторонніх AMS. Якщо оновлення таки потрібне — кандидат 01.07.02.00, але спершу
зберегти офлайн-пакет 01.05.00.00 для відкату через microSD.

Обидва принтери в LAN mode. Не припускати доступність хмарних функцій принтера.

---

## Жива система

`<bambuddy host>` — тільки локальна мережа (private IP),
TLS через Cloudflare DNS-01.

Доступ із сесії: MCP-сервер `bambuddy` у **proxy mode** — 4 мета-інструменти
(`list_categories`, `search_tools`, `execute_tool`, `find_printer`) поверх ~731
ендпоінта. Типовий ланцюжок: `find_printer` → `printer_id` → `search_tools` →
`execute_tool`.

**Три віртуальні принтери**, усі в режимі `queue`, усі запущені, статичні адреси:

| id | Назва | Модель | bind IP | target |
|---|---|---|---|---|
| 1 | Virtual P1S | P1S (`C12`) | `<LAN address>` | `printer_id: 2` |
| 2 | Virtual A1 Mini | A1 Mini (`N1`) | `<LAN address>` | `printer_id: 1` |
| 3 | Virtual H2D | H2D (`O1D`) | `<LAN address>` | не заданий |

Граблі, актуальні й для розробки:

- Зміна моделі VP **міняє серійник** — принтер доведеться перододавати в слайсері.
- Слайсеру треба імпортувати CA (`get_ca_certificate`); інсталятор слайсера
  відновлює оригінальний файл, тож повторювати після кожного оновлення.
- SSDP працює в межах LAN; через VPN/Docker принтер додається вручну за IP.
- **Tailscale живе на рівні Home Assistant, не всередині контейнера.**
  `get_tailscale_status` повертає `tailscale binary not found` — це очікувано за
  такої архітектури, а не поломка.

**Розетки** (`homeassistant`, `controls_printer_power: true`): `switch.<plug>`
для P1S, `switch.<plug>` для A1 mini. Уся автоматика вимкнена
(`auto_on`/`auto_off`/`schedule_enabled` = false) — ручне керування з обліком енергії.

---

## Інфраструктура

Bambuddy розгорнутий на **Raspberry Pi 5 Compute Module CM5-NANO-A** як застосунок
Home Assistant (`<home assistant host>`) через
[homeassistant-app-bambuddy](https://github.com/Spegeli/homeassistant-app-bambuddy).
Саме тому образ форку **мусить** мати `linux/arm64`.

Мережа: **статична** адресація для принтерів і VP (не DHCP). **Tailscale VPN**
піднятий на рівні Home Assistant. Домен резолвиться через Cloudflare DNS-01 у
private IP, LAN-only.

---

## Екосистема

| Сервіс | Роль | Документація |
|---|---|---|
| **Bambuddy** | основна система керування | https://wiki.bambuddy.cool/ |
| **Printago** | принтери та AMS, поточний стан | https://docs.printago.io/docs |
| **SpoolBuddy** | NFC + ваги для котушок | https://spoolbuddy.app/docs |
| **Filametrics** | облік філаменту, **виведено** | `https://app.myfilametrics.com/` |

**Printago** — доступ через скіл `printago` (CLI, потребує bash → **тільки вкладка
Code**). Мігрує в Bambuddy.

**Замість SpoolBuddy — ймовірно [ESPoolBuddy](https://github.com/CSchlipp/espoolbuddy).**
Рішення ще не остаточне.

**Пізніше — [Bambutton](https://github.com/EdwardChamberlain/bambutton)**
([вікі](https://wiki.bambuddy.cool/community/bambutton/)), фізична кнопка керування.

---

## Середовище

macOS 26.5.1 **arm64**, Homebrew у `/opt/homebrew`, Rosetta не використовується.

### Граблі, на які вже наступали

**Архітектура Python.** Після міграції з Intel-мака в `~/.local/share/uv/python/`
лишався x86_64-інтерпретатор, через який `uv` компілював нативні пакети з сирців
під не ту архітектуру. Симптом: `Building cryptography==...` замість завантаження
готового колеса, далі `ImportError: symbol not found in flat namespace '_BIO_ADDR_free'`.
Усунено. Якщо повториться:

```bash
uv python list                                   # шукати macos-x86_64 серед встановлених
uv run python -c "import sysconfig; print(sysconfig.get_platform())"
file $(which uv)
```

**Пін `mcp<2`.** `bambuddy-mcp` 0.2.0 оголошує `mcp>=1.0.0` без верхньої межі,
а mcp 2.0.0 додав залежність від `cryptography` та імпортує її в `__init__`:

```bash
uv tool install bambuddy-mcp --with "mcp<2"
```

**`uv tool install` замість `uvx`** для MCP-серверів: встановлений бінарник не
перерезолвлює залежності при кожному старті.

**Абсолютні шляхи в Claude Desktop.** Він не успадковує shell PATH —
`"command": "uvx"` дає `ENOENT`. Використовувати `/Users/jbo/.local/bin/bambuddy-mcp`.

### Тестове залізо SpoolBuddy

Плата для перевірки драйверів — **Raspberry Pi Model B+ (перше покоління), не Pi 2.**
HX711 працює тільки через mmap `/dev/gpiomem`; kiosk-режим на цьому залізі
неможливий — звідси й прапорець `--no-kiosk` в `spoolbuddy/install/install.sh`.

---

## Де що працює

| Задача | Поверхня |
|---|---|
| Bambuddy: принтери, VP, черга, інвентар | Home (чат) **і** Code |
| Printago, файли, скрипти, слайсинг-пайплайни | **тільки Code** |

`bambuddy` MCP доданий зі `--scope user` — доступний у будь-якому каталозі й на
обох поверхнях. Скіл `printago` працює через CLI, тож потребує bash.

Корисні MCP-інструменти поза мета-чотіркою (через `execute_tool`):
`list_virtual_printers`, `get_virtual_printer`, `diagnose_virtual_printer`,
`get_ca_certificate`, `get_tailscale_status`, `get_virtual_printer_models`.

---

## Безпека та обережність

- **Ключі API** лежать відкритим текстом у `~/.claude.json` і
  `claude_desktop_config.json`. Не комітити, не виводити в лог, не лишати в
  `~/.zsh_history` (`setopt HIST_IGNORE_SPACE` + провідний пробіл).
- **Не зупиняти й не змінювати активні друки** без явного підтвердження.
- Зміна моделі VP міняє серійник і ламає налаштування слайсера — не робити мимохідь.
- `update_virtual_printer` / `delete_virtual_printer` зачіпають робочу конфігурацію
  трьох VP — не викликати без явного підтвердження.
- **Розетки знімають живлення з принтера цілком.** Не вимикати під час друку,
  сушіння або поки хотенд гарячий.
- Bambuddy < 0.2.4.5 не застосовував тумблери прав ключа — будь-який валідний ключ
  міг керувати принтером.

---

## Структура репозиторію

| Каталог | Що там |
|---|---|
| `backend/` | FastAPI-застосунок, `app/api/routes/`, `app/core/config.py` (тут `APP_VERSION`), `tests/` |
| `frontend/` | Vite + React, збирається в статику всередині Dockerfile |
| `spoolbuddy/` | демон NFC + ваг для Pi: `daemon/`, `install/install.sh`, `tests/` — **моя основна зона правок** |
| `slicer-api/` | окремий sidecar (OrcaSlicer API), має власний compose |
| `install/` | інсталятори для хостів, включно з Windows |
| `.github/workflows/` | CI upstream + мої `docker-ghcr.yml` і `sync-upstream.yml` |
| `scripts/sync-upstream.sh` | синк дзеркал і тегів |

---

## Команди

```bash
ruff check backend/                     # лінт, як у CI
ruff format --check backend/
cd backend && python -m pytest tests/    # бекенд-тести
cd spoolbuddy && python -m pytest tests/ # тести драйверів SpoolBuddy
cd frontend && npm ci && npm run build   # фронт
```

Версію `ruff` брати з `requirements-dev.txt`, щоб збігалася з CI.

---

## Конвенції

- Апстрімні файли правити **мінімально** — кожна зайва правка це майбутній
  merge-конфлікт. Своє по можливості класти в нові файли.
- Коміти й PR — англійською, за `CONTRIBUTING.md` upstream.
- `GITHUB_REPO = "maziggy/bambuddy"` у `backend/app/core/config.py` навмисно не
  чіпаємо: вбудований апдейтер дивиться на upstream-релізи, і це дешевше, ніж
  конфлікт при кожному мержі.
- `docker-compose.yml` теж апстрімний і вказує на upstream-образ; свій запуск —
  через `docker-compose.fork.yml`.
