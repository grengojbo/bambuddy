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

| Принтер | AMS | Режим |
|---|---|---|
| Bambu Lab **P1S** | **AMS 2 Pro** | LAN-only |
| Bambu Lab **A1 mini** | BMCU (сторонній) | LAN-only |

Адреси, `printer_id` та імена — в Obsidian, нотатка «Мережа і залізо» (репозиторій
публічний). Усі інструменти приймають внутрішній `printer_id`, не IP і не назву;
отримати його з назви — `find_printer`. Ім'я принтера A1 mini збігається з назвою
локації зберігання філаменту — це різні сутності, не плутати при роботі з інвентарем.

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

Інстанція живе в локальній мережі (private IP), TLS через Cloudflare DNS-01.
URL і адреси — в нотатці «Мережа і залізо» в Obsidian.

Доступ із сесії: MCP-сервер `bambuddy` у **proxy mode** — 4 мета-інструменти
(`list_categories`, `search_tools`, `execute_tool`, `find_printer`) поверх ~731
ендпоінта. Типовий ланцюжок: `find_printer` → `printer_id` → `search_tools` →
`execute_tool`.

**Три віртуальні принтери** — P1S, A1 Mini і H2D (останній без target), усі в
режимі `queue`, усі запущені, на статичних адресах у LAN. Конкретні адреси й
серійники — в нотатці «Мережа і залізо».

Граблі, актуальні й для розробки:

- Зміна моделі VP **міняє серійник** — принтер доведеться перододавати в слайсері.
- Слайсеру треба імпортувати CA (`get_ca_certificate`); інсталятор слайсера
  відновлює оригінальний файл, тож повторювати після кожного оновлення.
- SSDP працює в межах LAN; через VPN/Docker принтер додається вручну за IP.
- **Tailscale живе на рівні Home Assistant, не всередині контейнера.**
  `get_tailscale_status` повертає `tailscale binary not found` — це очікувано за
  такої архітектури, а не поломка.

**Розетки** — по одній на принтер, тип `homeassistant`,
`controls_printer_power: true`. Уся автоматика вимкнена
(`auto_on`/`auto_off`/`schedule_enabled` = false): ручне керування з обліком
енергії. Entity ID — у нотатці «Мережа і залізо».

---

## Інфраструктура

Bambuddy розгорнутий на **Raspberry Pi 5 Compute Module CM5-NANO-A** як застосунок
Home Assistant через
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

### Нотатки — Obsidian

Робочі нотатки по Bambuddy живуть в Obsidian, не в репозиторії:

```
~/Library/Mobile Documents/iCloud~md~obsidian/Documents/Bamuddy/
  00-Bambuddy.md          доступи, посилання, помилки принтера, знахідки
  10-Розробка форку.md    сценарій розробки: гілки, dev-стек, реліз образу
  20-Мережа і залізо.md   IP, серійники VP, HA entity ID, хости
```

Формат — Obsidian: YAML-frontmatter із тегами, `[[wikilink]]` між нотатками,
callout-и (`> [!warning]`), українською. Нумерований префікс у назві задає
порядок; нова нотатка лінкується з `00-Bambuddy` в обидва боки.

**Там лежать паролі, ключі й адреси** — нічого звідти не переносити в
репозиторій і не цитувати в комітах чи PR. Форк **публічний**: IP, серійники
віртуальних принтерів, entity ID і хости в ньому не з'являються — їхнє місце в
нотатці «Мережа і залізо». У зворотний бік: якщо процес усталився, його місце в
цьому файлі, а нотатка лишається для чернеток і залізячних спостережень.

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
| `Makesurefile` + `makesure` | цілі розробки; раннер вендорений, 0.9.18 |
| `dev/tailscale-serve.json` | конфіг `tailscale serve` для dev-сайдкара |

---

## Локальна розробка

Усе через `makesure` (вендорений раннер у корені, `./makesure -l` — список цілей):

```bash
./makesure up          # бекенд + фронт у контейнерах → :8000 і :5173
./makesure ts          # те саме, ще й опубліковано в tailnet як bambuddy-dev
./makesure down        # зупинити все, разом із профілями
./makesure logs        # хвіст бекенду; logs-frontend — vite
./makesure restore     # залити бекап прода в локальну копію
./makesure check       # ruff + тести, як у CI
VERSION=1.2.5.5-jbo.2 ./makesure release
```

`restore` бере найсвіжіший `~/Downloads/bambuddy-backup*.zip` (перевизначити —
`ZIP=<шлях> ./makesure restore`), а якщо локального немає — пробує скачати з
прода. Після заливання переписує
`external_url` у базі на адресу цієї інстанції (`TS_HOSTNAME`, інакше
`localhost:5173`) — інакше сповіщення й QR-коди етикеток вели б у прод.
Решту прод-адрес (`ha_url`, `*_api_url`) лишає як є й друкує їх у звіті.
Пропустити крок — `--keep-settings`. Прапорець `--no-auth` додатково вимикає
логін у локальній копії: зручно, але тоді будь-який пристрій, що дістає цю
інстанцію (а з увімкненим tailnet-сайдкаром це вся мережа tailnet), керує
реальними принтерами без пароля. Автоматичне скачування
працює лише з сесією браузера: API-ключам `settings:backup` і `settings:restore`
заборонені в `_APIKEY_DENIED_PERMISSIONS` (`backend/app/core/auth.py`), тож
жодні галки ключа не допоможуть — ZIP зберігати з UI й передавати файлом:
`scripts/dev-restore.sh <file.zip>`.

Обидва сервіси — контейнери з бін-монтуванням робочого дерева: бекенд бере
рантайм із мого образу й піднімає uvicorn з `--reload`, фронт — `node:22` з
`vite`. Перебудовувати образ, щоб побачити зміну, не треба. Залежності фронту
переставляються автоматично, коли змінився `package-lock.json` (звіряється хеш
у `node_modules/.lock-hash`); примусово — `./makesure deps`.

Фронт запускається з `frontend/vite.config.dev.ts` (наш файл, апстрімний
`vite.config.ts` не чіпаємо): він перенаправляє проксі `/api` на сервіс
`bambuddy`, вмикає polling для вотчера (bind-mount на macOS не віддає
inotify-події) і, коли задано `TS_HOSTNAME`, дозволяє цей Host і переводить HMR
на `wss://…:443`.

`./makesure ts` піднімає сайдкар `tailscale/tailscale` з hostname `bambuddy-dev`
і `tailscale serve` на фронт — UI відкривається як
`https://bambuddy-dev.<tailnet>.ts.net` із справжнім сертифікатом, без
прокидання портів. Потрібні `TS_AUTHKEY` і `TS_HOSTNAME` у `.env` (він у
`.gitignore`; шаблон — у `.env.example`).

Три речі, на яких це спотикається: ключ має бути **reusable** (інакше другий
`up` не підніме вузол), у tailnet мають бути ввімкнені **HTTPS-сертифікати**
(без них `serve` на 443 не запрацює), і вузлу варто зняти термін дії ключа в
admin console, інакше він тихо випаде за пів року. Funnel навмисно не вмикаємо —
це виставило б dev-інстанцію з даними прода в публічний інтернет. Повна
інструкція — в Obsidian, «Розробка форку», розділ про Tailscale.

Контейнер бере рантайм із `ghcr.io/grengojbo/bambuddy`, але монтує `backend/` з
робочого дерева поверх коду в образі та запускає uvicorn з `--reload`. Дані —
у `./dev-data` (ігнорується git).

Обмеження, про які треба пам'ятати:

- **Bridge mode, не host** — на macOS host-мережі немає. Вихід на принтери за
  статичними IP працює (статус, камера, керування), а SSDP-виявлення й віртуальні
  принтери — ні: їм потрібні власні статичні біндинги в LAN.
- Після `dev-restore.sh` локальна копія — точний клон прода й **чіпляється до тих
  самих реальних принтерів**. Що вимкнути (VP, розетки, автоматику) — вирішувати
  в UI перед тим, як лишати її працювати.
- Слайсер-сайдкари існують тільки під `linux/amd64`.

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
- `docker-compose.yml` — виняток із правила вище: він переписаний під локальну
  розробку (мій образ, монтування `backend/`, `--reload`, дані в `./dev-data`).
  Конфліктів при мержі не буде: `sync-upstream.sh` ставить для цього шляху
  git-драйвер `ours` (у `.git/info/attributes`, щоб не чіпати апстрімний
  `.gitattributes`), тож наша версія лишається автоматично. Зворотний бік —
  корисні апстрімні правки цього файлу теж мовчки відкидаються, тому скрипт
  друкує список таких комітів для ручного перенесення.
- Слайсер-сайдкари в нашому compose не заведені: вони потрібні лише коли
  ввімкнено «Use Slicer API», існують тільки під `linux/amd64` і живуть в
  апстрімному `slicer-api/docker-compose.yml`, який ми не чіпаємо.
