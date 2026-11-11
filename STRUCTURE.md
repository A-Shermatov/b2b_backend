# BrandMarket Backend — структура и статус

Стек: **FastAPI + asyncpg + PostgreSQL + Redis + MinIO/S3**.  
Префикс API: `/api/v1`. Документация: `/docs`, `/redoc`. Health: `GET /health`.

Легенда статусов:

| Статус | Значение |
|--------|----------|
| ✅ DONE | Работает end-to-end |
| 🟡 PARTIAL | Есть код/флоу, но mock / gaps / не production-ready |
| ❌ NOT DONE | Нет реализации |

---

## 1. Общая архитектура

Modular monolith в пакете `app/`, как HR: роутеры → OOP-сервисы → raw SQL.

```
app/
├── main.py                 # FastAPI, CORS, /uploads, lifespan
├── config.py
├── database.py             # asyncpg pool
├── deps.py                 # JWT, actor/company context
├── enums.py
├── core/                   # exceptions, security, middleware
├── db/                     # dump + SQL-патчи, backfill actors
├── utils/                  # redis, storage (S3), audit
└── modules/                # auth, actors, companies, catalog, rfqs,
                            # proposals, contracts, payments, finance,
                            # reviews, notifications, subscription, admin
migrations/
├── dump.sql
└── *.sql                   # инкрементальные патчи
```

Поток запроса:

```
Client → app/modules/*/api → app.deps → services (SQL) → PostgreSQL
                                      ↓
                              utils/storage, redis, WS push
```

---

## 2. Дерево каталогов

| Путь | Статус | Назначение |
|------|--------|------------|
| `app/modules/auth/` | ✅ | Регистрация, login, refresh, me, switch actor/company |
| `app/modules/companies/` | ✅ | Профиль, сертификаты, команда |
| `app/modules/catalog/` | ✅ | Категории, items, публичный каталог, жалобы |
| `app/modules/rfqs/` | ✅ | Draft → publish → close, attachments, invite |
| `app/modules/proposals/` | ✅ | Shortlist / reject / accept, messages |
| `app/modules/contracts/` | ✅ | Chat, files, submissions, disputes |
| `app/modules/payments/` | ✅ | Escrow milestones |
| `app/modules/finance/` | ✅ | Supplier finance + platform ledger |
| `app/modules/reviews/` | ✅ | Buyer reviews |
| `app/modules/notifications/` | ✅ | DB + WS `/api/v1/ws/notifications` |
| `app/modules/subscription/` | ✅ | Тарифы поставщика |
| `app/modules/admin/` | ✅ | Модерация users/companies/catalog/rfqs/proposals/contracts/disputes/finance/reports |
| `app/db/` | ✅ | `dump.sql` на пустую БД + SQL-миграции |
| `tests/` | ✅ | admin / buyer / supplier / health / payments |
| `scripts/seed.py` | ✅ | Демо-аккаунты |
| `docker-compose.yml` | 🟡 | Postgres + Redis + MinIO (**без API**) |
| `Dockerfile` + `docker-entrypoint.sh` | ✅ | Образ API (`app.main:app`, schema + seed) |

---

## 3. Доменные модули

| Модуль | Статус | Комментарий |
|--------|--------|-------------|
| Auth / Actors | ✅ | JWT, multi-actor, switch role/company |
| Companies | ✅ | Профиль, сертификаты, команда, роли |
| Catalog | ✅ | CRUD items, publish/archive, модерация |
| RFQs | ✅ | Draft → publish → close |
| Proposals | ✅ | Shortlist / reject / accept |
| Contracts | ✅ | Chat, files, submissions, disputes |
| Payments / escrow | 🟡 | Milestone fund/approve + **mock confirm** |
| Reviews | ✅ | Buyer reviews |
| Notifications | ✅ | DB + WS; email/SMS нет |
| Subscription | 🟡 | Activate/cancel сразу, ledger `gateway=mock` |
| Supplier finance | 🟡 | Destinations, withdrawals, invoices; payout = mock |
| Admin | ✅ | Dashboard + moderation |
| Marketplace orders | ❌ удалён | Не возвращаем |

---

## 4. API endpoints (кратко)

Все пути относительно `/api/v1`, кроме `/health`.

### Auth — `/auth` ✅

| Method | Path |
|--------|------|
| POST | `/register`, `/login`, `/refresh`, `/logout` |
| GET/PATCH | `/me` |
| POST | `/switch-actor`, `/switch-company`, `/activate-role` |
| GET/PUT | `/me/page` — страничка частного лица (чем занимается, навыки, ставка, портфолио); POST `/me/page/image` |
| GET | `/invitations` — приглашения в компании, ждущие ответа |
| POST | `/invitations/{id}/accept`, `/invitations/{id}/decline`; по ссылке: `/invitations/accept`, `/invitations/decline` |

`POST /register` принимает `account_type` (`individual` / `company`), `company_title`,
`headline` и `invite_token`: компания получает кабинет сразу, приглашённый попадает в команду.

### Public — `/public` ✅

| Method | Path |
|--------|------|
| GET | `/categories`, `/suppliers`, `/suppliers/{id}`, `.../catalog`, `.../reviews` |
| GET | `/companies/{id}`, `.../reviews`, `.../catalog` |
| GET | `/catalog`, `/catalog/items/{id}`, `/rfqs` |
| GET | `/invitations/{token}` — что показывает ссылка-приглашение |

### Buyer — `/buyer` ✅

Companies, RFQs, proposals, contracts, payments (`POST /mock/confirm/{milestone_id}`), reviews, notifications.

### Supplier — `/supplier` ✅

Companies, catalog, RFQ board, proposals, contracts, payments, finance, subscription, notifications.

### Admin — `/admin` ✅

Dashboard, users, companies, catalog, rfqs, proposals, contracts, disputes, finance (+ export), reports.

### Прочее

| | Path | Статус |
|--|------|--------|
| Catalog report | `POST /catalog/items/{id}/reports` | ✅ |
| WebSocket | `WS /ws/notifications?token=...` | ✅ |
| Health | `GET /health` | ✅ |

---

## 5. Auth, RBAC, middleware

| Компонент | Статус | Где |
|-----------|--------|-----|
| JWT access/refresh (HS256) | ✅ | `app/core/security_core.py` |
| bcrypt passwords | ✅ | |
| Bearer + `get_current_user` | ✅ | `app/deps.py` |
| Headers `X-Actor-Id`, `X-Company-Id` | ✅ | |
| Buyer / Supplier context | ✅ | `app/deps.py` |
| Роли в компании | ✅ | director / admin / moderator / accountant / viewer (только просмотр) |
| Приглашения в команду | ✅ | `company_invitations`: человек входит в компанию после согласия, срок 14 дней |
| Admin gate | ✅ | `require_admin` |
| Login rate limit | ✅ | Redis или in-memory |
| Refresh blacklist | ✅ | Redis |
| CORS + access/audit middleware | ✅ | `app/core/middleware.py` |
| Email verification / SMTP | ❌ | |

---

## 6. Инфраструктура

| Компонент | Статус | Детали |
|-----------|--------|--------|
| PostgreSQL | ✅ | compose порт **5433** |
| Redis | ✅ | rate limit, token blacklist; memory fallback |
| MinIO / S3 | ✅ | `app/utils/storage.py` + local `/uploads` |
| WebSocket notifications | ✅ | in-process (без Redis pub/sub) |
| Stripe / YooKassa | ❌ | только enum |
| Email / SMS / push | ❌ | |
| Celery / workers | ❌ | всё в request |
| API в docker-compose | ❌ | API поднимается отдельно (`uvicorn app.main:app`) |
| Схема БД | ✅ | `migrations/dump.sql` + `migrations/*.sql` |

---

## 7. Тесты

| Область | Путь |
|---------|------|
| Health | `tests/test_health.py` |
| Buyer API / RFQ / contracts / IDOR / RBAC | `tests/buyer/` |
| Supplier API / RBAC | `tests/supplier/` |
| Admin | `tests/admin/` |
| Payment terms | `tests/test_payment_terms.py` |

---

## 8. Сводка

### Сделано

- Auth + multi-actor / multi-company
- Buyer: RFQ → proposals → contract → chat/files/submissions/disputes
- Supplier: catalog, RFQ board, proposals, contracts, finance
- Admin-модерация
- Public directory
- In-app уведомления + WebSocket
- S3/MinIO или local uploads
- Стек как у HR: `app/`, raw SQL, без ORM

### Частично

- Платежи / escrow — только **mock**
- Подписки и выводы — mock gateway
- Docker — инфраструктура есть, API в compose нет
- WS — один процесс

### Не сделано

- Реальные PSP (Stripe / YooKassa) и webhooks
- Фоновые воркеры
- Email / SMS / push
- Email verification

---

*Quick start — в `Readme.md`.*
