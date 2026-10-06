# Архитектура

Локальное desktop-приложение (Windows), Python 3.11+, PySide6, SQLite через
SQLAlchemy 2.x. Собирается в `.exe` через PyInstaller (точка входа `run.py`).

## Слои

```
run.py                    # запуск: python run.py
app/
  main.py                 # QApplication, окно
  core/                   # config, события, логирование, планировщик, секреты, часы
  domain/                 # enums + value objects (без зависимостей от Qt/БД)
  database/               # SQLAlchemy models, session, repositories
  services/               # бизнес-логика (без Qt-виджетов, кроме сигналов в ядре)
  integrations/
    steam/                # SteamWebClient (официальный API), симулятор, метаданные, правила семьи
    funpay/               # FunPayAdapter: интерфейс + capabilities + ручной и (опц.) golden_key режимы
  workers/                # QRunnable-воркеры (никогда не блокируют GUI)
  ui/                     # PySide6: окно, страницы, виджеты, модели таблиц
tests/                    # pytest: критическая логика
docs/                     # RESEARCH.md, ARCHITECTURE.md
```

Правила зависимостей: `ui → services → (database, integrations) → domain`.
GUI не знает про способ получения данных из внешних сервисов — только через
сервисы/адаптеры. Все долгие операции — в `QThreadPool` воркерах; мутации GUI —
только из главного потока через сигналы `EventBus`.

## Схема БД (основные таблицы)

```
steam_accounts      id, steam_id64, display_name, avatar_url, status, region,
                    family_role, family_joined_at, family_left_at, last_sync_at,
                    last_error, notes
games               id, app_id UNIQUE, name, sort_as, is_free, release_date,
                    genres(json), categories(json), price_rub, currency,
                    discount_pct, header_image_url, store_url,
                    requires_third_party_launcher, requires_third_party_account,
                    has_multiplayer, has_coop, anticheat, review_score,
                    review_count, approx_owners, tags(json),
                    eligibility_status, eligibility_reason, eligibility_source,
                    eligibility_checked_at, eligibility_override (NULL/OK/NO),
                    demand_score, demand_grade, demand_breakdown(json),
                    commercial_score, score_computed_at, notes
account_games       id, account_id FK, app_id FK→games.app_id, source(own/family),
                    playtime_min, last_played_at        # «сырой» срез синхронизации
game_licenses       id, game_id FK, account_id FK, source, acquired_at,
                    hidden_from_family BOOL, notes      # 1 копия = 1 запись
clients             id, funpay_username UNIQUE, display_name, first_order_at,
                    last_order_at, total_spent, notes, problems, blacklisted
orders              id, funpay_order_id NULLABLE UNIQUE, client_id FK,
                    game_id FK NULL, bundle_id FK NULL, price, currency,
                    status, purchased_at, access_starts_at, access_ends_at,
                    assigned_license_id FK NULL, notes, created_by(manual/funpay)
access_leases       id, client_id, order_id, game_id, owner_account_id,
                    license_id UNIQUE per active lease, starts_at, expires_at,
                    terminated_at, status(active/expired/completed/cancelled)
listings            id, game_id FK NULL, bundle_id FK NULL, funpay_listing_id NULL,
                    title, description, price, access_days, status(draft/active/paused),
                    sales_count, revenue, updated_at
bundles             id, name, description, suggested BOOL, suggested_reason,
                    price, status
bundle_games        bundle_id, game_id
messages            id, client_id, order_id NULL, direction(out/in),
                    template_key, body, status(drafted/approved/sent/manual),
                    created_at, sent_at
price_history       id, game_id, price, discount_pct, recorded_at
sync_history        id, kind(steam/funpay), started_at, finished_at, status,
                    details(json)
audit_log           id, ts, actor(user/app/scheduler), action, entity, entity_id,
                    details(json)
notifications       id, ts, kind, title, body, read BOOL, acknowledged BOOL
settings            key PK, value(json)
```

### Ключевые инварианты

1. **1 копия игры = 1 `game_licenses`**; `GameLicense` может быть отдана одной
   аренде (`access_leases`) за раз: частичный уникальный индекс по `license_id`
   для активных аренд + повторная проверка в `LicenseAllocator` внутри
   транзакции.
2. Дубликат `app_id` с двух аккаунтов → одна запись `games` + две `game_licenses`.
3. F2P-игра не попадает в коммерческий каталог: `demand_grade='FREE'`,
   `sellable=False` (вычисляется, не хранится).
4. Игра с `eligibility_status ∉ {AVAILABLE, MANUAL_CHECK}` не продаётся.
5. Истёкшая аренда ⇒ лицензия автоматически освобождается
   (пересчёт по расписанию + лениво при чтении).

## Сервисы

| Сервис | Ответственность |
|---|---|
| `LibraryService` | синхронизация библиотек, слияние `app_id`, лицензии-копии |
| `FamilyEligibilityService` | статусы Steam Families: правила → датасет → кэш → ручной оверрайд; источник и дата проверки; без связи с GUI |
| `DemandService` | Demand Score 0–100 + Commercial Score + грейды S/A/B/C/D/FREE; веса — из настроек; каждый фактор — отдельный класс-провайдер |
| `LicenseAllocator` | выбор свободной копии для игры; никогда не выдаёт занятую |
| `AccessService` | создание/продление/завершение аренд, таймеры, освобождение |
| `OrderService` | заказы: ручной ввод и импорт из адаптера, статусная модель |
| `ListingService` | генерация текстов объявлений/шаблонов, привязка к играм/пакетам |
| `BundleService` | ручные + автопредложенные пакеты по жанрам и спросу |
| `AnalyticsService` | выручка, топ игр, загрузка лицензий, рекомендации (отделены от фактов) |
| `NotificationService` | центр уведомлений + desktop tray; события из EventBus |
| `AuditService` | журнал всех значимых действий |
| `PricingService` | учёт цен и истории (внешние цены конкурентов — только через адаптер) |

## Безопасность

* Секреты (Steam API key, FunPay golden_key) — только в **keyring**
  (Windows Credential Manager); в БД и настройках — ссылки вида
  `keyring://steam/api_key`. Пароли не запрашиваются и не хранятся.
* `DRY RUN` в настройках: чтение/анализ/превью работают, любые внешние
  изменения — запрещены (адаптеры отдают превью, воркеры пишут в audit).
* Все рискованные операции — диалог подтверждения; отправка сообщений по
  умолчанию в режиме `Manual approval`.

## Планировщик

`QTimer`-реестр задач + `QThreadPool`-воркеры. Интервалы из настроек.
Повторные ошибки — экспоненциальный backoff (base × 2^n с капом) и
уведомление «Требуется ручное действие».

## Расширяемость (задел, без реализации)

`FunPayAdapter` и `TelegramBot`/`DiscordBot` как будущие адаптеры того же
интерфейса уведомлений; провайдеры факторов `DemandService` регистрируются
динамически; репозитории позволяют заменить источник данных без правки
сервисов.
