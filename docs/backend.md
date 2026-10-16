# BrandMarket Backend

Гайд для бэкенд-разработчика: как устроен проект, какие сущности есть и какой API уже реализован.

Интерактивная схема запросов и ответов: [http://localhost:8000/docs](http://localhost:8000/docs). Этот файл объясняет домен и границы модулей. Точные поля тел запросов смотри в Pydantic-схемах модуля и в OpenAPI.

Префикс всех маршрутов: `/api/v1`.

## Что это за система

B2B-маркетплейс. Заказчик (`buyer`) публикует запрос (RFQ). Исполнитель (`supplier`) отвечает предложением. Заказчик принимает предложение — появляется контракт и этапы оплаты (milestones). Деньги проходят через эскроу: заказчик фондирует этап, исполнитель сдаёт работу, заказчик подтверждает, средства уходят исполнителю. Площадка модерирует компании, каталог, споры и жалобы.

Один и тот же человек может быть и заказчиком, и исполнителем (`role = both`). Действие всегда выполняется от конкретного **актора**, а не просто от пользователя.

## Как читать код

Модульный монолит. Точка входа — `app/main.py`: lifespan, middleware, регистрация модулей.

Каждый модуль лежит в `app/modules/<имя>/`:

| Папка / файл | Зачем |
|---|---|
| `router.py` | Функция `register(app, prefix)`. Подключает роутеры к приложению. |
| `api/common.py` | `APIRouter`, общие импорты. |
| `api/create.py`, `read.py`, `update.py` | HTTP-обработчики. Тонкие: проверка зависимости и вызов сервиса. |
| `schemas/` | Pydantic-модели запроса и ответа. |
| `services/` | SQL и бизнес-правила. Часто миксины `*CreateMixin`, `*ReadMixin`, `*UpdateMixin`, собранные в `base.py`. |

База — PostgreSQL через **asyncpg**, без ORM. Запросы пишутся SQL-строками. Схема: `migrations/dump.sql`. В `development` при старте `app/db/schema.py` накатывает дамп и патчи из `app/db/patches.py`.

Зависимости FastAPI и контекст запроса — `app/deps.py`. Права по матрице — `app/core/permissions.py`. Ошибки — `app/core/exceptions.py` (`UnauthorizedError` 401, `ForbiddenError` 403, `NotFoundError` 404, `ConflictError` 409, `ValidationError` 422).

## Модель доступа

Три слоя, их нельзя путать.

**Пользователь** (`users`). Платформенная роль `user_role`:

| Роль | Смысл |
|---|---|
| `buyer` | Заказчик |
| `supplier` | Исполнитель |
| `both` | Обе стороны |
| `admin`, `moderator` | Админка |
| `superadmin` | Админка, все permissions без проверки матрицы |

Статус пользователя: `pending`, `active`, `blocked`. Заблокированный не логинится и не проходит `get_current_user`.

**Актор** (`actors`) — от чьего лица идёт сделка.

| Поле | Значения |
|---|---|
| `kind` | `individual` (человек) или `company` (компания) |
| `side` | `buyer` или `supplier` |

У пользователя с ролью `both` есть отдельные акторы на каждую сторону. Компания тоже представлена актором.

**Роль в компании** (`company_users.role`): `director`, `admin`, `moderator`, `accountant`. Для индивидуального актора проверка компании пропускается. Наборы в `app/deps.py`:

| Константа | Кто входит |
|---|---|
| `FULL_COMPANY_ROLES` | `director`, `admin` |
| `RFQ_ROLES` | `director`, `admin`, `moderator` |
| `FINANCE_ROLES` | `director`, `admin`, `accountant` |

Поверх этого на части ручек стоит матрица прав: `buyer_perm("rfqs.create")`, `supplier_perm("catalog.publish")`. Имя права — `ресурс.действие`. `superadmin` проходит всегда. Остальным нужно явное разрешение в `role_permissions`.

### Заголовки

| Заголовок | Когда |
|---|---|
| `Authorization: Bearer <access_token>` | Почти все закрытые ручки |
| `X-Actor-Id` | Каким актором действовать. Для buyer/supplier-ручек обязателен, если актор не выводится однозначно |
| `X-Company-Id` | Какая компания активна, если актор компании |

Buyer-ручка требует актор со `side = buyer` и роль пользователя `buyer` или `both`. Supplier-ручка — симметрично для `supplier`.

При логине можно передать `role`: `buyer` (заказчик) или `supplier` (исполнитель). Если у аккаунта нет этой стороны, ответ 403. Выбранная сторона кладётся в access- и refresh-токен как `side` и возвращается в `TokenResponse.role`. Для админов поле необязательное.

## Пагинация списков

Коллекции, которые раньше были JSON-массивом, отвечают объектом `Page` из `app/core/pagination.py`. Query-параметры те же, что у `PaginationDep` в `app/deps.py`: `page` (с 1) и `page_size` (от 1 до 100, по умолчанию 20). Срез считается в SQL (`COUNT` и `LIMIT`/`OFFSET`), а не после загрузки всей таблицы.

```json
{
  "items": [],
  "total": 0,
  "page": 1,
  "page_size": 20,
  "total_pages": 0
}
```

При `total = 0` поле `total_pages` тоже `0`. В таблицах ниже такие ручки помечены словом «страница».

Страницей отвечают:

- свои компании и публичный список исполнителей;
- витрина, каталог актора, каталог компании и свои карточки исполнителя;
- свои RFQ, доска исполнителя и публичные RFQ;
- отклики на RFQ и свои отклики исполнителя;
- списки контрактов обеих сторон;
- история платежей и ожидающие платежи обеих сторон;
- реквизиты, выводы и счета исполнителя;
- свои отзывы и публичные отзывы актора и компании;
- уведомления обеих сторон;
- `GET /admin/roles`;
- `GET /admin/companies/pending-verification`.

Массивом остаются дерево категорий, переписка по отклику и контракту, этапы оплаты одного контракта и вложенные списки внутри карточки (команда, файлы, таймлайн).

Админские списки пользователей, компаний, каталога, RFQ, откликов, контрактов, споров, финансов, жалоб, журнала и эскроу пагинируются отдельно. У них поле называется `pages`, а не `total_pages`, и у части ответов рядом есть счётчики вроде `status_counts`. Пустая выборка там даёт `pages: 1`. Категории админки — это `{ "items": [...] }` без страницы. Сотрудники — `{ "items", "total" }` без `page`.

## Сквозной сценарий сделки

```text
Компания / профиль
    → RFQ (черновик → публикация)
        → Proposal исполнителя
            → Accept заказчиком
                → Contract + milestones
                    → Fund → Submission → Approve → выплата
                        → Review (только completed)
```

Если стороны не сходятся по сдаче, открывается спор. Его закрывает админ.

## Модули и API

Одинаковые buyer- и supplier-ручки ниже схлопнуты в одну строку с пометкой стороны. Полный путь: `/api/v1/<сторона>/<путь>`.

### Auth — `app/modules/auth`

Пользователь, сессия, переключение актора. Префикс `/api/v1/auth`.

| Метод | Путь | Что делает |
|---|---|---|
| POST | `/register` | Регистрация. Роль `buyer`, `supplier` или `both`. Сразу выдаёт токены и создаёт индивидуальных акторов. |
| POST | `/login` | Вход. Опционально `role`: `buyer` или `supplier`. |
| POST | `/refresh` | Новая пара токенов. Сторона из refresh сохраняется, если она ещё доступна. |
| POST | `/logout` | Refresh в чёрный список. 204. |
| GET | `/me` | Профиль, акторы, компании, capabilities, активный актор. |
| PATCH | `/me` | Имя, фамилия, телефон. |
| POST | `/switch-actor` | Сменить активного актора. |
| POST | `/switch-company` | Сменить компанию (и её актор). |
| POST | `/activate-role` | Включить вторую сторону: `buyer` ↔ `supplier`, роль становится `both`. Создаёт индивидуального актора этой стороны. |

Логин ограничен по IP (`rate_limit_login_per_minute`).

### Companies — `app/modules/companies`

Компания заказчика или исполнителя, команда, сертификаты, публичный каталог поставщиков.

| Метод | Путь | Сторона | Что делает |
|---|---|---|---|
| GET | `/companies/me` | buyer, supplier | Страница компаний текущего пользователя |
| POST | `/companies` | buyer, supplier | Создать компанию (мастер) |
| PATCH | `/companies/{company_id}` | buyer, supplier | Обновить карточку |
| POST | `/companies/{company_id}/certificates` | buyer, supplier | Сертификат |
| POST | `/companies/{company_id}/team` | buyer, supplier | Добавить сотрудника |
| DELETE | `/companies/{company_id}/team/{target_user_id}` | buyer, supplier | Убрать из команды. 204 |
| GET | `/public/suppliers` | публично | Страница исполнителей. Фильтры `q` и `category` |
| GET | `/public/suppliers/{actor_id}` | публично | Карточка исполнителя |
| GET | `/public/companies/{company_id}` | публично | Карточка компании |

Директор компании не удаляется как обычный участник. Верификация компании (`verification_status`) меняется админом, не этой ручкой.

### Catalog — `app/modules/catalog`

Товары и услуги исполнителя, дерево категорий, жалобы на карточку.

Статусы карточки: `draft`, `pending_review`, `changes_requested`, `active`, `hidden`, `archived`, `deleted`.

| Метод | Путь | Кто | Что делает |
|---|---|---|---|
| GET | `/public/categories` | публично | Дерево категорий, без страницы |
| GET | `/public/catalog` | публично | Страница витрины. Фильтры `q` и `category` |
| GET | `/public/catalog/items/{item_id}` | публично | Карточка |
| GET | `/public/suppliers/{actor_id}/catalog` | публично | Страница каталога актора |
| GET | `/public/companies/{company_id}/catalog` | публично | Страница каталога компании. Нет supplier-актора — пустая страница |
| GET | `/supplier/catalog/items` | supplier | Страница своих карточек. Фильтр `status` |
| GET | `/supplier/catalog/items/{item_id}` | supplier | Своя карточка |
| POST | `/supplier/catalog/items` | supplier, `catalog.create` | Создать. `active` и `pending_review` упираются в лимит подписки |
| PATCH | `/supplier/catalog/items/{item_id}` | supplier, `catalog.update` | Править |
| POST | `/supplier/catalog/items/{item_id}/publish` | supplier, `catalog.publish` | Опубликовать |
| POST | `/supplier/catalog/items/{item_id}/archive` | supplier, `catalog.update` | В архив |
| DELETE | `/supplier/catalog/items/{item_id}` | supplier, `catalog.delete` | Удалить. 204 |
| POST | `/catalog/items/{item_id}/reports` | любой залогиненный | Жалоба на карточку |

### RFQ — `app/modules/rfqs`

Запрос заказчика: товар (`product`) или услуга (`service`).

Статусы: `draft`, `published`, `receiving_proposals`, `supplier_selected`, `contract_created`, `in_progress`, `completed`, `cancelled`, `expired`, `disputed`, `archived`.

| Метод | Путь | Кто | Что делает |
|---|---|---|---|
| GET | `/buyer/rfqs` | buyer | Страница своих запросов. Фильтр `tab` |
| GET | `/buyer/rfqs/{rfq_id}` | buyer | Карточка |
| POST | `/buyer/rfqs` | buyer, `rfqs.create` | Черновик |
| PATCH | `/buyer/rfqs/{rfq_id}` | buyer, `rfqs.update` | Правка |
| POST | `/buyer/rfqs/{rfq_id}/publish` | buyer, `rfqs.publish` | Опубликовать |
| POST | `/buyer/rfqs/{rfq_id}/close` | buyer, `rfqs.close` | Закрыть |
| POST | `/buyer/rfqs/{rfq_id}/attachments` | buyer, `rfqs.update` | Вложение |
| DELETE | `/buyer/rfqs/{rfq_id}/attachments/{attachment_id}` | buyer, `rfqs.update` | Снять вложение |
| POST | `/buyer/rfqs/{rfq_id}/invite` | buyer, `rfqs.invite` | Пригласить исполнителей |
| GET | `/supplier/rfqs/board` | supplier | Страница доски |
| GET | `/supplier/rfqs/{rfq_id}` | supplier | Запрос, если он виден исполнителю |
| POST | `/supplier/rfqs/{rfq_id}/proposals` | supplier, `proposals.create` | Отклик. Создаёт proposal, не живёт в модуле proposals |
| GET | `/public/rfqs` | публично | Страница открытых запросов |

Приватный или invite-only запрос на доске исполнителя виден только приглашённому.

### Proposals — `app/modules/proposals`

Отклик исполнителя на RFQ. Создание — в модуле RFQ (строка выше). Здесь список, переписка и решение заказчика.

Статусы: `submitted`, `viewed`, `shortlisted`, `accepted`, `rejected`, `withdrawn`.

| Метод | Путь | Кто | Что делает |
|---|---|---|---|
| GET | `/buyer/rfqs/{rfq_id}/proposals` | buyer | Страница откликов. Просмотр помечает `submitted` как `viewed` |
| POST | `/buyer/proposals/{proposal_id}/shortlist` | buyer, `proposals.accept` | В шорт-лист |
| POST | `/buyer/proposals/{proposal_id}/reject` | buyer, `proposals.accept` | Отклонить |
| POST | `/buyer/proposals/{proposal_id}/accept` | buyer, `proposals.accept` | Принять и создать контракт |
| GET | `/buyer/proposals/{proposal_id}/messages` | buyer | Переписка |
| POST | `/buyer/proposals/{proposal_id}/messages` | buyer, `proposals.update` | Сообщение |
| GET | `/supplier/proposals` | supplier | Страница своих откликов. `accepted` и `archived` в список не попадают |
| PATCH | `/supplier/proposals/{proposal_id}` | supplier, `proposals.update` | Правка, пока можно |
| POST | `/supplier/proposals/{proposal_id}/withdraw` | supplier, `proposals.update` | Отозвать |
| GET | `/supplier/proposals/{proposal_id}/messages` | supplier | Переписка |
| POST | `/supplier/proposals/{proposal_id}/messages` | supplier, `proposals.update` | Сообщение |

`accept` — граница между торгами и сделкой. Дальше источник правды — контракт.

### Contracts — `app/modules/contracts`

Сделка между buyer-актором и supplier-актором. Появляется из принятого proposal.

Статусы: `pending_payment`, `active`, `delivered`, `completed`, `cancelled`, `disputed`.

| Метод | Путь | Кто | Что делает |
|---|---|---|---|
| GET | `/contracts` | buyer, supplier | Страница своих контрактов |
| GET | `/contracts/{contract_id}` | buyer, supplier | Карточка |
| GET | `/contracts/{contract_id}/messages` | buyer, supplier | Чат сделки |
| POST | `/contracts/{contract_id}/messages` | обе стороны, `contracts.update` | Сообщение |
| POST | `/contracts/{contract_id}/messages/read` | обе стороны, `contracts.update` | Прочитать |
| POST | `/contracts/{contract_id}/messages/{message_id}/delivered` | обе стороны, `contracts.update` | Отметка доставки |
| POST | `/contracts/{contract_id}/files` | обе стороны, `contracts.update` | Файл к сделке |
| POST | `/contracts/{contract_id}/submissions` | supplier, `contracts.update` | Сдача этапа |
| POST | `/contracts/{contract_id}/submissions/{submission_id}/approve` | buyer, `contracts.update` | Принять сдачу |
| POST | `/contracts/{contract_id}/submissions/{submission_id}/reject` | buyer, `contracts.update` | Вернуть на доработку |
| POST | `/contracts/{contract_id}/dispute` | обе стороны, `contracts.update` | Открыть спор |

Отзыв можно оставить только когда контракт `completed`, и только buyer-актор этого контракта. Один отзыв на пару контракт + автор.

### Payments — `app/modules/payments`

Этапы оплаты контракта (milestones) и эскроу.

Статусы этапа: `pending`, `awaiting_payment`, `funded`, `in_progress`, `submitted`, `approved`, `released`, `refunded`, `disputed`, `overdue`, `cancelled`.

| Метод | Путь | Кто | Что делает |
|---|---|---|---|
| GET | `/payments/config` | buyer | Какой шлюз включён: `alif` или `mock` |
| GET | `/payments/contracts/{contract_id}/milestones` | buyer, supplier | План этапов одного контракта, без страницы |
| GET | `/payments/history` | buyer, supplier | Страница истории |
| GET | `/payments/pending` | buyer, supplier | Страница ожидающих действий |
| GET | `/payments/balance` | supplier | Баланс исполнителя |
| POST | `/payments/milestones/{milestone_id}/fund` | buyer, `payments.fund` | Заморозить оплату этапа. Заголовок `Idempotency-Key` необязателен |
| POST | `/payments/alif/orders/{order_id}/sync` | buyer, `payments.fund` | Подтянуть статус заказа Alif |
| POST | `/payments/alif/callback` | шлюз Alif | Вебхук. В OpenAPI не публикуется. Если Alif выключен — 404 |
| POST | `/payments/milestones/{milestone_id}/approve` | buyer, `payments.approve` | Подтвердить и отпустить средства |
| POST | `/payments/mock/confirm/{milestone_id}` | buyer, `payments.fund` | Подтверждение мок-платежа для разработки |

Если `alif_enabled` выключен, фондирование идёт через mock (`POST /payments/mock/confirm/{milestone_id}`). Комиссия сделки берётся из настроек платформы. Проводки смотрит админ в finance и escrow.

### Finance — `app/modules/finance`

Вывод денег исполнителя. Это не оплата заказчиком, а кошелёк поставщика.

| Метод | Путь | Кто | Что делает |
|---|---|---|---|
| GET | `/finance/destinations` | supplier | Страница реквизитов |
| POST | `/finance/destinations` | supplier, `finance.create` | Добавить реквизиты |
| GET | `/finance/withdrawals` | supplier | Страница заявок на вывод |
| POST | `/finance/withdrawals` | supplier, `finance.create` | Создать вывод |
| GET | `/finance/invoices` | supplier | Страница счетов |

### Subscription — `app/modules/subscription`

Тариф исполнителя. Лимит каталога проверяется при создании карточки в статусах `active` и `pending_review`.

| Метод | Путь | Кто | Что делает |
|---|---|---|---|
| GET | `/subscription` | supplier | Текущая подписка |
| POST | `/subscription/activate` | supplier, `subscription.update` | Включить план |
| POST | `/subscription/cancel` | supplier, `subscription.update` | Отменить |

### Reviews — `app/modules/reviews`

Отзыв заказчика об исполнителе после завершённого контракта. После вставки пересчитывается средний рейтинг компании исполнителя.

| Метод | Путь | Кто | Что делает |
|---|---|---|---|
| GET | `/buyer/reviews` | buyer | Страница своих отзывов |
| POST | `/buyer/reviews` | buyer, `reviews.create` | Оставить отзыв |
| GET | `/public/suppliers/{actor_id}/reviews` | публично | Страница отзывов об акторе |
| GET | `/public/companies/{company_id}/reviews` | публично | Страница отзывов о компании |

### Notifications — `app/modules/notifications`

Лента уведомлений и websocket.

| Метод | Путь | Кто | Что делает |
|---|---|---|---|
| GET | `/notifications` | buyer, supplier | Страница уведомлений |
| PATCH | `/notifications/{notification_id}/read` | buyer, supplier | Прочитать одно |
| POST | `/notifications/read-all` | buyer, supplier | Прочитать все. 204 |
| WS | `/api/v1/ws/notifications?token=<access_token>` | по токену | Пуш. Без токена сокет закрывается с кодом 4401. Каждые 30 с сервер шлёт `{"event":"ping"}` |

### Roles — `app/modules/roles`

Матрица прав сотрудников админки (`roles.scope = staff`). Все ручки под `/api/v1/admin`. Чтение списка и карточки — `roles.view`, изменение — `roles.manage`. Роль `superadmin` в список не входит и удалить её нельзя. В элементе страницы есть `users_count`.

| Метод | Путь | Что делает |
|---|---|---|
| GET | `/admin/roles` | Страница ролей сотрудников. Нужен `roles.view` |
| GET | `/admin/roles/{role_id}` | Роль |
| POST | `/admin/roles` | Создать. 201 |
| PUT | `/admin/roles/{role_id}` | Переименовать / обновить |
| DELETE | `/admin/roles/{role_id}` | Удалить. 204 |
| POST | `/admin/roles/assign` | Назначить роль пользователю. 204 |
| GET | `/admin/permissions/matrix/catalog` | Справочник ресурсов и действий для UI матрицы |
| GET | `/admin/roles/{role_id}/matrix` | Матрица роли |
| PUT | `/admin/roles/{role_id}/matrix` | Сохранить матрицу |

Ресурсы матрицы и русские подписи — `RESOURCE_ALIASES_RU` в `app/core/permissions.py`. Туда же входят `admin_staff`, `audit_logs` и `platform_settings`.

### Settings — `app/modules/settings`

Комиссия и потолок суммы контракта. Публичная ручка без авторизации, админские — под `require_admin`.

| Метод | Путь | Кто | Что делает |
|---|---|---|---|
| GET | `/public/platform-settings` | публично | Процент комиссии, минимум и максимальная сумма контракта |
| GET | `/admin/settings` | admin, `platform_settings.view` | Те же поля плюс кто и когда менял, и флаг `can_update` |
| PUT | `/admin/settings` | admin, `platform_settings.update` | Сохранить `commission_percent`, `commission_min`, `max_contract_amount` |

### Admin — `app/modules/admin`

Модерация. Весь роутер закрыт `require_admin`: роли `admin`, `superadmin`, `moderator`. Отдельные действия дополнительно проверяют permission (`admin_catalog.delete` и похожие). Префикс `/api/v1/admin`.

Списки ниже, кроме очереди верификации, отвечают своим конвертом: `items`, `total`, `page`, `page_size`, `pages`. У пользователей и компаний рядом есть `status_counts`. Очередь верификации использует общий `Page` с `total_pages`. Query у всех один: `page` и `page_size`.

| Метод | Путь | Что делает |
|---|---|---|
| GET | `/dashboard` | Сводка |
| GET | `/users` | Пользователи |
| PATCH | `/users/{user_id}/status` | `pending` / `active` / `blocked` |
| GET | `/companies` | Компании |
| GET | `/companies/pending-verification` | Страница очереди верификации (`total_pages`) |
| GET | `/companies/{company_id}` | Карточка: команда, контракты, отзывы, чеклист |
| POST | `/companies/{company_id}/verify` | Одобрить или отклонить верификацию |
| POST | `/companies/{company_id}/action` | `approve`, `reject`, `request_documents`, `block`, `deactivate`, `reactivate` |
| GET | `/catalog`, `/catalog/{item_id}` | Модерация витрины |
| POST | `/catalog/{item_id}/action` | `approve`, `hide`, `request_changes`, `delete` |
| GET | `/rfqs`, `/rfqs/{rfq_id}` | Запросы |
| POST | `/rfqs/{rfq_id}/action` | `hide`, `close`, `delete`, `warn_buyer` |
| GET | `/proposals`, `/proposals/{proposal_id}` | Отклики |
| POST | `/proposals/{proposal_id}/action` | `delete`, `investigate`, `block_supplier` |
| GET | `/contracts`, `/contracts/{contract_id}` | Контракты |
| POST | `/contracts/{contract_id}/action` | `freeze`, `cancel`, `force_complete`, `open_investigation` |
| GET | `/disputes`, `/disputes/{dispute_id}` | Споры: стороны, переписка, файлы, эскроу, таймлайн |
| POST | `/disputes/{dispute_id}/action` | `release_funds`, `refund_buyer`, `partial_refund`, `request_evidence`, `close_case` |
| GET | `/finance`, `/finance/{payment_id}` | Платежи платформы |
| GET | `/finance/export` | Выгрузка |
| POST | `/finance/{payment_id}/action` | `refund`, `retry`, `mark_paid` |
| GET | `/reports` | Жалобы на каталог, RFQ и proposal. Конверт с `pages` |
| GET | `/reports/{target_type}/{report_id}` | `target_type`: `catalog`, `rfq`, `proposal` |
| POST | `/reports/{target_type}/{report_id}/action` | `dismiss`, `warn`, `suspend`, `delete` |
| GET | `/staff` | Сотрудники админки, `{ items, total }`. Фильтр `query`. Право `admin_staff.view` |
| POST | `/staff` | Создать сотрудника. 201. `admin_staff.create` |
| PATCH | `/staff/{user_id}` | Обновить сотрудника. `admin_staff.update` |
| GET | `/logs` | Журнал. Конверт с `pages` и `audience_counts`. Фильтры `audience`, `kind`, `section`, `status`, `period`, `query`. Право `audit_logs.view` |
| GET | `/logs/{log_id}` | Карточка записи журнала |
| GET | `/categories` | Дерево категорий для модерации, только `items`. `admin_catalog.view` |
| POST | `/categories` | Создать категорию. 201 |
| PATCH | `/categories/{category_id}` | Переименовать, сменить slug или родителя |
| DELETE | `/categories/{category_id}` | Удалить категорию |
| GET | `/escrow` | Эскроу: сводка, счётчики `held` / `disputed` / `awaiting` и страница контрактов с полем `pages`. `admin_finance.view` |
| GET | `/analytics` | Сводка за `period`: `30d`, `90d` или `12m`. `admin_dashboard.view` |

Жалобу на карточку каталога создаёт пользователь (`POST /catalog/items/{item_id}/reports`). Жалобы на RFQ и proposal в админке читаются, отдельных публичных ручек создания в коде нет: таблицы `rfq_reports` и `proposal_reports` наполняются тем кодом, который пишет в них напрямую.

## Где что менять

| Задача | Куда смотреть |
|---|---|
| Новый URL | `api/` модуля и `router.py`. Импорт `api/create.py` в `router.py` через `# noqa: F401` нужен: декоратор вешается при импорте. |
| Новый список со страницей | `PaginationDep` на ручке и `fetch_page` / `build_page` в сервисе. Тип ответа — `Page[...]` из `app/core/pagination.py`. |
| Правило сделки | `services/` того модуля, чья сущность меняет статус. Принятие отклика — `proposals`, сдача и спор — `contracts`, деньги этапа — `payments`. |
| Кто имеет право | `app/deps.py` (актор и роль в компании) и `buyer_perm` / `supplier_perm` на ручке. |
| Новое право в матрице | Сид permissions и `RESOURCE_ALIASES_RU`. |
| Колонка или enum | `migrations/dump.sql` для новой базы и патч в `app/db/patches.py` для уже живой. |
| Файл пользователя | `app/utils/storage.py`. В ответе наружу отдаётся публичный URL из `FILES_BASE_URL`, в базе хранится ключ объекта. |

## Инфраструктура вокруг запроса

Middleware в `app/main.py`, порядок важен:

| Middleware | Зачем |
|---|---|
| `CollapseDuplicateSlashesMiddleware` | Схлопывает `//` в пути |
| CORS | Отдаёт клиенту `Authorization` и `X-Access-Token` |
| `RequestPublicOriginMiddleware` | Публичный origin для ссылок на файлы |
| `AccessLogMiddleware` | Лог доступа |
| `TokenRefreshMiddleware` | На мутирующих запросах может выдать новый access в `X-Access-Token`. Login, register, refresh, logout пропускает |
| `HttpAuditMiddleware` | Аудит мутаций в `audit_logs` |

`GET /health` — живость процесса, без базы. Статика загрузок, если файл лёг локально: `/uploads`.

Redis используется для rate limit и чёрного списка refresh. Если Redis не поднят, лимит логина в тестах подменяется.

## Локальный запуск

```bash
cp .env.example .env
docker compose up -d
pip install -e ".[dev]"
python scripts/seed.py
uvicorn app.main:app --reload --port 8000
```

Postgres на хосте слушает порт **5433**. Демо-аккаунты после сида: `admin@example.com` / `Admin123!`, `buyer@example.com` / `Buyer123!`, `supplier@example.com` / `Supplier123!`.

Тесты: `python -m pytest`. Фикстуры входа — `tests/conftest.py`: `buyer_auth` логинится с `role=buyer` и подставляет `X-Actor-Id`.
