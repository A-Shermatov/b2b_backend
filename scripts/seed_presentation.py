from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from uuid import NAMESPACE_URL, uuid5

from app.core.security_core import hash_password
from app.database import pool


COUNT = 12
PRESENTATION_SEED_KEY = "presentation_seed_v1"
PRESENTATION_SUPPLEMENT_KEY = "presentation_seed_supplement_v3"

SUPPLIER_NAMES = [
    "ТехноСнаб", "Цифровые решения", "ПрофиЛогистик", "СтройГарант",
    "Маркетинг Плюс", "Душанбе Софт", "Восток Трейд", "Умный Склад",
    "Новый Веб", "ПромСервис", "Альфа Консалт", "Сомон Диджитал",
]
BUYER_NAMES = [
    "Сомон Трейд", "Ориён Групп", "Душанбе Ритейл", "Памир Инвест",
    "Фароз Строй", "Навруз Маркет", "Вахдат Текстиль", "Авесто Фуд",
    "Зарафшон Девелопмент", "Рушд Сервис", "Арча Плюс", "Бадахшан Тур",
]
ITEM_TITLES = [
    "Разработка корпоративного сайта", "Поставка ноутбуков бизнес-класса",
    "Продвижение бренда в социальных сетях", "Комплексная SEO-оптимизация",
    "Доставка сборного груза Душанбе — Худжанд", "Ремонт офисного помещения",
    "Разработка мобильного приложения", "Серверное оборудование для офиса",
    "Настройка контекстной рекламы", "Поставка складских стеллажей",
    "Техническая поддержка веб-проекта", "Дизайн и печать каталога",
]
RFQ_TITLES = [
    "Обновление корпоративного сайта", "Закупка рабочих ноутбуков",
    "Продвижение новой линейки продуктов", "Перевозка оборудования в Худжанд",
    "Ремонт торгового помещения", "Мобильное приложение для клиентов",
    "Поставка серверов и сетевого оборудования", "SEO для интернет-магазина",
    "Регулярная доставка продукции по регионам", "Брендинг нового продукта",
    "Техническая поддержка сайта", "Проектирование складского комплекса",
]
CATEGORY_SLUGS = ["web", "it", "marketing", "seo", "logistics", "renovation", "mobile", "it", "marketing", "ftl", "web", "construction"]
DEMO_NAMES = [
    "Алина", "Бехруз", "Дилором", "Фарид", "Мадина", "Комрон",
    "Нигина", "Сорбон", "Манижа", "Рустам", "Шабнам", "Фирдавс",
]
IMAGE_IDS = [
    "1498050108023-c5249f4df085",
    "1518770660439-4636190af475",
    "1553877522-43269d4ea984",
    "1551288049-bebda4e38f71",
    "1601584115197-04ecc0da31d7",
    "1504307651254-35680f356dfd",
    "1516321318423-f06f85e504b3",
    "1581091226825-a6a2a5aee158",
    "1519389950473-47ba0277781c",
    "1586528116311-ad8dd3c8310d",
    "1521737711867-e3b97375f902",
    "1547658719-da2b51169166",
]
COMPANY_IMAGE_IDS = [
    "1497366216548-37526070297c",
    "1556761175-b413da4baf72",
    "1497366811353-6870744d04b2",
    "1600880292203-757bb62b4baf",
    "1521737711867-e3b97375f902",
    "1497366754035-f200968a6e72",
    "1551836022-d5d88e9218df",
    "1497366754035-f200968a6e72",
    "1560472354-b33ff0c44a43",
    "1556761175-b413da4baf72",
    "1486406146926-c627a92ad1ab",
    "1553877522-43269d4ea984",
]


def _timestamp(days_ago: int = 0) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days_ago)


def _image_url(image_id: str, *, width: int = 1200) -> str:
    return f"https://images.unsplash.com/photo-{image_id}?auto=format&fit=crop&w={width}&q=85"


async def _get_or_create_demo_user(conn, *, index: int, role: str, password_hash: str) -> int:
    email = f"presentation.{role}.{index + 1:02}@example.com"
    user_id = await conn.fetchval("SELECT id FROM users WHERE email = $1", email)
    if user_id is None:
        first_name = DEMO_NAMES[index]
        user_id = await conn.fetchval(
            """
            INSERT INTO users (
                email, password_hash, first_name, last_name, role, role_id,
                status, created_at, updated_at
            ) VALUES (
                $1, $2, $3, 'Демо', $4::user_role,
                (SELECT id FROM roles WHERE name = $4::text),
                'active'::user_status, NOW(), NOW()
            ) RETURNING id
            """,
            email,
            password_hash,
            first_name,
            role,
        )
    actor_id = await conn.fetchval(
        """
        SELECT id FROM actors
        WHERE user_id = $1 AND kind::text = 'individual' AND side::text = $2
        LIMIT 1
        """,
        user_id,
        role,
    )
    if actor_id is None:
        await conn.execute(
            """
            INSERT INTO actors (
                kind, side, user_id, company_id, display_name, trust_level,
                is_active, created_at, updated_at
            ) VALUES (
                'individual'::actor_kind, $1::actor_side, $2, NULL, $3,
                'basic'::trust_level, TRUE, NOW(), NOW()
            )
            """,
            role,
            user_id,
            f"{DEMO_NAMES[index]} Демо",
        )
    return user_id


async def _get_or_create_company(conn, *, title: str, actor_type: str, owner_id: int, index: int) -> tuple[int, int]:
    row = await conn.fetchrow(
        """
        SELECT c.id AS company_id, a.id AS actor_id
        FROM companies c
        JOIN actors a ON a.company_id = c.id AND a.kind::text = 'company'
            AND a.side::text = $2 AND a.is_active = TRUE
        WHERE c.title = $1
        """,
        title,
        actor_type,
    )
    if row:
        return row["company_id"], row["actor_id"]

    company = await conn.fetchrow(
        """
        INSERT INTO companies (
            title, actor_type, owner_id, legal_name, description, country, city,
            verification_status, operational_status, rating, created_at, updated_at
        )
        VALUES (
            $1, $2::actor_type, $3, $4, $5, 'Таджикистан', $6,
            $7::verification_status, 'active'::company_operational_status,
            $8, NOW() - ($9::int * INTERVAL '1 day'), NOW()
        )
        RETURNING id
        """,
        title,
        actor_type,
        owner_id,
        f"ООО «{title}»",
        f"Демо-компания для презентации BrandMarket: {title}.",
        ("Душанбе", "Худжанд", "Бохтар")[index % 3],
        "pending" if index == 10 else "verified",
        4.0 + (index % 10) / 10,
        index * 9,
    )
    company_id = company["id"]
    await conn.execute(
        """
        INSERT INTO company_profiles (
            company_id, founded_year, employees_count, languages, industries
        ) VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (company_id) DO NOTHING
        """,
        company_id,
        2012 + index,
        8 + index * 3,
        ["ru", "tg"] if index % 2 == 0 else ["ru", "en"],
        ["IT", "Торговля"] if actor_type == "supplier" else ["Торговля", "Строительство"],
    )
    await conn.execute(
        """
        INSERT INTO company_stats (
            company_id, completed_contracts, active_contracts,
            disputes_count, average_rating
        ) VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (company_id) DO NOTHING
        """,
        company_id,
        4 + index * 2,
        1 + index % 4,
        index % 3,
        4.0 + (index % 10) / 10,
    )
    await conn.execute(
        """
        INSERT INTO company_users (company_id, user_id, role)
        VALUES ($1, $2, 'director'::company_role)
        ON CONFLICT DO NOTHING
        """,
        company_id,
        owner_id,
    )
    actor = await conn.fetchrow(
        """
        INSERT INTO actors (
            kind, side, user_id, company_id, display_name, trust_level,
            is_active, created_at, updated_at
        )
        VALUES (
            'company'::actor_kind, $1::actor_side, NULL, $2, $3,
            $4::trust_level, TRUE, NOW(), NOW()
        )
        RETURNING id
        """,
        actor_type,
        company_id,
        title,
        "basic" if index == 10 else "verified",
    )
    return company_id, actor["id"]


async def _seed(conn) -> dict[str, int]:
    buyer = await conn.fetchrow(
        """
        SELECT u.id AS user_id, a.id AS actor_id
        FROM users u JOIN actors a ON a.user_id = u.id
        WHERE u.email = 'buyer@example.com' AND a.kind::text = 'individual'
          AND a.side::text = 'buyer'
        LIMIT 1
        """
    )
    supplier = await conn.fetchrow(
        """
        SELECT u.id AS user_id, a.id AS actor_id
        FROM users u JOIN actors a ON a.user_id = u.id
        WHERE u.email = 'supplier@example.com' AND a.kind::text = 'individual'
          AND a.side::text = 'supplier'
        LIMIT 1
        """
    )
    admin_id = await conn.fetchval("SELECT id FROM users WHERE email = 'admin@example.com'")
    if not buyer or not supplier or not admin_id:
        raise RuntimeError("Run scripts/seed.py before presentation data seeding")

    buyer_user_id = buyer["user_id"]
    supplier_user_id = supplier["user_id"]
    buyer_actors = [buyer["actor_id"]]
    supplier_actors = [supplier["actor_id"]]
    password_hash = hash_password("Demo123!")
    buyer_user_ids = [buyer_user_id]
    supplier_user_ids = [supplier_user_id]
    for index in range(1, COUNT):
        buyer_user_ids.append(
            await _get_or_create_demo_user(
                conn, index=index, role="buyer", password_hash=password_hash
            )
        )
        supplier_user_ids.append(
            await _get_or_create_demo_user(
                conn, index=index, role="supplier", password_hash=password_hash
            )
        )
    buyer_companies: list[int] = []
    supplier_companies: list[int] = []

    for index in range(COUNT):
        buyer_company, buyer_actor = await _get_or_create_company(
            conn,
            title=BUYER_NAMES[index],
            actor_type="buyer",
            owner_id=buyer_user_ids[index],
            index=index,
        )
        supplier_company, supplier_actor = await _get_or_create_company(
            conn,
            title=SUPPLIER_NAMES[index],
            actor_type="supplier",
            owner_id=supplier_user_ids[index],
            index=index,
        )
        buyer_companies.append(buyer_company)
        buyer_actors.append(buyer_actor)
        supplier_companies.append(supplier_company)
        supplier_actors.append(supplier_actor)

    categories = await conn.fetch("SELECT id, slug FROM categories")
    category_ids = {row["slug"]: row["id"] for row in categories}
    items: list[int] = []
    for index, title in enumerate(ITEM_TITLES):
        category_id = category_ids.get(CATEGORY_SLUGS[index])
        if category_id is None:
            category_id = await conn.fetchval("SELECT id FROM categories ORDER BY id LIMIT 1")
        row = await conn.fetchrow(
            """
            INSERT INTO catalog_items (
                actor_id, type, category_id, title, description, status,
                created_at, updated_at
            )
            SELECT $1, $2::catalog_item_type, $3, $4::varchar(255), $5,
                   $6::item_status, NOW() - ($7::int * INTERVAL '1 day'), NOW()
            WHERE NOT EXISTS (
                SELECT 1 FROM catalog_items WHERE title = $4::varchar(255) AND actor_id = $1
            )
            RETURNING id
            """,
            supplier_actors[index],
            "service" if index % 2 == 0 else "product",
            category_id,
            title,
            f"Презентационное описание: {title}. Работаем по договору и с закрывающими документами.",
            "pending_review" if index == 10 else "active",
            index * 3,
        )
        item_id = row["id"] if row else await conn.fetchval(
            "SELECT id FROM catalog_items WHERE title = $1 AND actor_id = $2",
            title,
            supplier_actors[index],
        )
        items.append(item_id)
        await conn.execute(
            """
            INSERT INTO item_pricing (
                item_id, pricing_type, currency, fixed_price, hourly_rate,
                monthly_rate, tiers
            ) VALUES ($1, 'fixed'::pricing_type, 'TJS', $2, NULL, NULL, $3::jsonb)
            ON CONFLICT DO NOTHING
            """,
            item_id,
            2500 + index * 1750,
            json.dumps([{"min_qty": 1, "price": 2500 + index * 1750}]),
        )
        await conn.execute(
            "INSERT INTO item_stats (item_id, views, leads) SELECT $1, $2, $3 WHERE NOT EXISTS (SELECT 1 FROM item_stats WHERE item_id = $1)",
            item_id,
            80 + index * 37,
            3 + index,
        )

    rfq_ids: list[str] = []
    rfq_statuses = ["receiving_proposals"] * 8 + ["draft", "published", "completed", "supplier_selected"]
    for index, title in enumerate(RFQ_TITLES):
        rfq_id = str(uuid5(NAMESPACE_URL, f"brandmarket-presentation-rfq-{index + 1}"))
        category_id = category_ids.get(CATEGORY_SLUGS[index], next(iter(category_ids.values())))
        await conn.execute(
            """
            INSERT INTO rfqs (
                id, actor_id, created_by, type, title, description, category_id,
                budget_type, budget_from, budget_to, currency, deadline,
                visibility, status, quantity, delivery_country, delivery_city,
                delivery_date, project_duration, start_date, team_size_required,
                experience_required, catalog_item_id, created_at, updated_at
            ) VALUES (
                $1, $2, $3, $4::rfq_type, $5, $6, $7, 'range'::budget_type,
                $8, $9, 'TJS', $10, 'public'::rfq_visibility, $11::rfq_status,
                $12, 'Таджикистан', $13, $14, $15, $16, $17, $18, $19,
                NOW() - ($20::int * INTERVAL '1 day'), NOW()
            ) ON CONFLICT (id) DO NOTHING
            """,
            rfq_id,
            buyer_actors[index + 1],
            "Иван Заказчик",
            "service" if index % 2 == 0 else "product",
            title,
            f"Презентационная заявка №{index + 1}. Просим приложить сроки, состав работ и коммерческое предложение.",
            str(category_id),
            5000 + index * 1000,
            12000 + index * 1800,
            (date.today() + timedelta(days=14 + index)).isoformat(),
            rfq_statuses[index],
            1 + index % 20,
            ("Душанбе", "Худжанд", "Бохтар")[index % 3],
            (date.today() + timedelta(days=30 + index)).isoformat(),
            f"{2 + index % 6} недели",
            date.today().isoformat(),
            1 + index % 5,
            "Опыт от 2 лет, договор и подтверждённое портфолио",
            items[index],
            index * 2,
        )
        rfq_ids.append(rfq_id)

    proposal_ids: list[int] = []
    proposal_statuses = ["accepted", "accepted", "accepted", "accepted", "shortlisted", "viewed", "submitted", "submitted", "rejected", "withdrawn", "submitted", "viewed"]
    for index, rfq_id in enumerate(rfq_ids):
        proposal_id = await conn.fetchval(
            """
            INSERT INTO proposals (
                rfq_id, supplier_actor_id, price, currency, delivery_time,
                message, status, created_at
            ) VALUES (
                $1, $2, $3, 'TJS'::currency, $4, $5,
                $6::proposal_status, NOW() - ($7::int * INTERVAL '1 day')
            ) RETURNING id
            """,
            rfq_id,
            supplier_actors[index + 1],
            7000 + index * 1200,
            f"{7 + index % 20} дней",
            f"Подготовили решение по заявке. Готовы начать в согласованные сроки, предложение №{index + 1}.",
            proposal_statuses[index],
            max(1, index),
        )
        proposal_ids.append(proposal_id)
        await conn.execute(
            """
            INSERT INTO proposal_messages (proposal_id, sender_id, text, created_at)
            VALUES
                ($1, $2, $3, NOW() - INTERVAL '2 hours'),
                ($1, $4, $5, NOW() - INTERVAL '1 hour')
            """,
            proposal_id,
            buyer_user_ids[index],
            "Уточните, пожалуйста, состав работ и гарантийный срок.",
            supplier_user_ids[index],
            "Включили этапы, сроки и гарантию в предложение. Готовы обсудить детали.",
        )

    contract_ids: list[int] = []
    contract_statuses = ["completed", "completed", "completed", "active", "active", "pending_payment", "delivered", "disputed", "cancelled", "active", "completed", "pending_payment"]
    for index, proposal_id in enumerate(proposal_ids):
        contract_id = await conn.fetchval(
            """
            INSERT INTO contracts (
                rfq_id, proposal_id, buyer_actor_id, supplier_actor_id, title,
                description, agreed_amount, currency, start_date, due_date,
                payment_type, status, created_at, commission_amount,
                commission_percent, commission_min
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, 'TJS'::contract_currency,
                $8, $9, 'milestone'::payment_type, $10::contract_status,
                NOW() - ($11::int * INTERVAL '1 day'), $12, 5, 0
            ) RETURNING id
            """,
            rfq_ids[index],
            proposal_id,
            buyer_actors[index + 1],
            supplier_actors[index + 1],
            f"Договор по проекту «{RFQ_TITLES[index]}»",
            "Демонстрационный договор: результат, сроки и приёмка зафиксированы сторонами.",
            7000 + index * 1200,
            (date.today() - timedelta(days=20 - index)).isoformat(),
            (date.today() + timedelta(days=20 + index)).isoformat(),
            contract_statuses[index],
            index * 4,
            (7000 + index * 1200) * 0.05,
        )
        contract_ids.append(contract_id)
        plan_id = await conn.fetchval(
            "INSERT INTO payment_plans (contract_id, payment_type) VALUES ($1, 'milestone'::plan_payment_type) RETURNING id",
            contract_id,
        )
        for milestone_index, status in enumerate(("released", "funded" if index < 6 else "pending")):
            amount = (7000 + index * 1200) / 2
            await conn.execute(
                """
                INSERT INTO payment_milestones (
                    payment_plan_id, contract_id, title, percentage, amount,
                    trigger, status, commission_amount
                ) VALUES ($1, $2, $3, 50, $4, $5, $6::milestone_status, $7)
                """,
                plan_id,
                contract_id,
                ("Аванс и начало работ", "Передача результата")[milestone_index],
                amount,
                ("После подписания договора", "После приёмки результата")[milestone_index],
                status,
                amount * 0.05,
            )
        conversation_id = await conn.fetchval(
            "INSERT INTO conversations (contract_id) VALUES ($1) RETURNING id",
            contract_id,
        )
        await conn.execute(
            """
            INSERT INTO messages (
                conversation_id, sender_id, text, created_at, status,
                delivered_at, viewed_at
            ) VALUES
                ($1, $2, $3, NOW() - INTERVAL '2 hours', 'viewed'::message_delivery_status, NOW(), NOW()),
                ($1, $4, $5, NOW() - INTERVAL '1 hour', 'delivered'::message_delivery_status, NOW(), NULL)
            """,
            conversation_id,
            buyer_user_ids[index],
            "Подтверждаем график работ и порядок приёмки.",
            supplier_user_ids[index],
            "График принят. Первый этап готов к проверке.",
        )
        await conn.execute(
            """
            INSERT INTO work_submissions (
                contract_id, type, note, status, submitted_at, file_names, assets
            ) VALUES (
                $1, 'work'::submission_type, $2, $3::submission_status,
                NOW() - ($4::int * INTERVAL '1 day'), $5, $6::jsonb
            )
            """,
            contract_id,
            f"Результат этапа по договору №{index + 1} готов к проверке.",
            ("accepted", "pending", "rejected")[index % 3],
            index + 1,
            [f"demo-result-{index + 1}.pdf"],
            json.dumps([{"name": f"demo-result-{index + 1}.pdf", "url": "/files/demo-result.pdf"}]),
        )

    for index in range(COUNT):
        conversation_id = await conn.fetchval(
            """
            INSERT INTO direct_conversations (buyer_actor_id, supplier_actor_id)
            VALUES ($1, $2)
            ON CONFLICT (buyer_actor_id, supplier_actor_id) DO UPDATE
                SET buyer_actor_id = EXCLUDED.buyer_actor_id
            RETURNING id
            """,
            buyer_actors[index + 1],
            supplier_actors[index + 1],
        )
        await conn.execute(
            """
            INSERT INTO direct_messages (
                conversation_id, sender_id, text, status, delivered_at,
                viewed_at, created_at
            ) VALUES
                ($1, $2, $3, 'viewed'::message_delivery_status, NOW(), NOW(), NOW() - INTERVAL '2 hours'),
                ($1, $4, $5, 'delivered'::message_delivery_status, NOW(), NULL, NOW() - INTERVAL '1 hour')
            """,
            conversation_id,
            buyer_user_ids[index],
            "Здравствуйте! Интересует ваше предложение по проекту.",
            supplier_user_ids[index],
            "Добрый день! Направили детали и готовы ответить на вопросы.",
        )

    for index, contract_id in enumerate(contract_ids[:10]):
        await conn.execute(
            """
            INSERT INTO reviews (
                contract_id, reviewer_actor_id, target_actor_id, rating, comment,
                created_at, service_rating, delivery_rating, quality_rating, tags
            ) VALUES ($1, $2, $3, $4, $5, NOW() - ($6::int * INTERVAL '1 day'), $4, $4, $4, $7)
            ON CONFLICT DO NOTHING
            """,
            contract_id,
            buyer_actors[index + 1],
            supplier_actors[index + 1],
            3 + index % 3,
            f"Работы по проекту завершены. Коммуникация и сроки соответствовали ожиданиям ({index + 1}).",
            index,
            ["в срок", "качественно"] if index % 2 == 0 else ["рекомендуем"],
        )

    for index in range(COUNT):
        await conn.execute(
            """
            INSERT INTO notifications (user_id, company_id, type, title, body, href, read, created_at)
            VALUES ($1, $2, $3::notification_type, $4, $5, $6, $7, NOW() - ($8::int * INTERVAL '1 hour'))
            """,
            buyer_user_ids[index] if index % 2 == 0 else supplier_user_ids[index],
            buyer_companies[index] if index % 2 == 0 else supplier_companies[index],
            ("rfq", "proposal", "contract", "payment")[index % 4],
            ("Новый отклик", "Обновление договора", "Платёж по этапу", "Статус заявки")[index % 4],
            f"Есть обновление по демонстрационному сценарию №{index + 1}.",
            "/customer/rfqs" if index % 2 == 0 else "/supplier/proposals",
            index % 3 == 0,
            index * 7,
        )

    reasons = ["spam", "fraud", "counterfeit", "abuse", "other"]
    for index in range(COUNT):
        report_status = "open" if index < 8 else "resolved"
        resolved_at = None if report_status == "open" else _timestamp(index + 1)
        await conn.execute(
            """
            INSERT INTO catalog_item_reports (item_id, reporter_user_id, reason, details, status, created_at, resolved_at, resolved_by_id)
            VALUES ($1, $2, $3::catalog_item_report_reason, $4, $5::catalog_item_report_status, NOW() - ($6::int * INTERVAL '1 day'), $7::timestamptz, $8)
            """,
            items[index],
            buyer_user_ids[index],
            reasons[index % len(reasons)],
            f"Демонстрационная жалоба на карточку каталога №{index + 1}.",
            report_status,
            index + 1,
            resolved_at,
            admin_id if resolved_at else None,
        )
        await conn.execute(
            """
            INSERT INTO rfq_reports (rfq_id, reporter_user_id, reason, details, status, created_at, resolved_at, resolved_by_id)
            VALUES ($1, $2, $3::rfq_report_reason, $4, $5::rfq_report_status, NOW() - ($6::int * INTERVAL '1 day'), $7::timestamptz, $8)
            """,
            rfq_ids[index],
            supplier_user_ids[index],
            reasons[index % len(reasons)],
            f"Демонстрационная жалоба на заявку №{index + 1}.",
            report_status,
            index + 2,
            resolved_at,
            admin_id if resolved_at else None,
        )
        await conn.execute(
            """
            INSERT INTO proposal_reports (proposal_id, reporter_user_id, reason, details, status, created_at, resolved_at, resolved_by_id)
            VALUES ($1, $2, $3::proposal_report_reason, $4, $5::proposal_report_status, NOW() - ($6::int * INTERVAL '1 day'), $7::timestamptz, $8)
            """,
            proposal_ids[index],
            buyer_user_ids[index],
            reasons[index % len(reasons)],
            f"Демонстрационная жалоба на предложение №{index + 1}.",
            report_status,
            index + 3,
            resolved_at,
            admin_id if resolved_at else None,
        )

    for index, contract_id in enumerate(contract_ids):
        dispute_status = "open" if index == 0 else "under_review" if index == 1 else "resolved"
        await conn.execute(
            """
            INSERT INTO disputes (
                contract_id, status, opened_by_actor_id, buyer_statement,
                supplier_statement, resolution, resolution_note, resolved_at,
                resolved_by_id, created_at, updated_at
            ) VALUES (
                $1, $2::dispute_status, $3, $4, $5,
                $6::dispute_resolution, $7, $8::timestamptz, $9,
                NOW() - ($10::int * INTERVAL '1 day'), NOW()
            )
            """,
            contract_id,
            dispute_status,
            buyer_actors[index + 1],
            f"Заказчик просит проверить этап поставки по договору №{index + 1}.",
            f"Исполнитель приложил подтверждение выполнения работ по договору №{index + 1}.",
            None if index < 2 else "release_funds",
            None if index < 2 else "Результат принят после дополнительной проверки.",
            None if index < 2 else _timestamp(index + 1),
            None if index < 2 else admin_id,
            index + 1,
        )

    for index in range(COUNT):
        ticket_status = "open" if index == 0 else "closed"
        ticket_id = await conn.fetchval(
            """
            INSERT INTO support_tickets (user_id, side, status, assigned_admin_id, last_message_at, created_at, closed_at)
            VALUES ($1, $2, $3::support_ticket_status, $4, NOW() - ($5::int * INTERVAL '1 hour'), NOW() - ($5::int * INTERVAL '1 day'), $6::timestamptz)
            RETURNING id
            """,
            buyer_user_ids[index] if index % 2 == 0 else supplier_user_ids[index],
            "buyer" if index % 2 == 0 else "supplier",
            ticket_status,
            admin_id,
            index + 1,
            None if ticket_status == "open" else _timestamp(index + 1),
        )
        await conn.execute(
            """
            INSERT INTO support_messages (ticket_id, sender_id, text, status, delivered_at, viewed_at, created_at)
            VALUES
                ($1, $2, $3, 'viewed'::message_delivery_status, NOW(), NOW(), NOW() - INTERVAL '2 hours'),
                ($1, $4, $5, 'delivered'::message_delivery_status, NOW(), NULL, NOW() - INTERVAL '1 hour')
            """,
            ticket_id,
            buyer_user_ids[index] if index % 2 == 0 else supplier_user_ids[index],
            f"Здравствуйте, подскажите по заказу №{index + 1}.",
            admin_id,
            "Добрый день! Проверяем информацию и вернёмся с ответом.",
        )

    for index in range(COUNT):
        await conn.execute(
            """
            INSERT INTO purchase_approvals (
                company_id, requester_user_id, title, supplier_name, amount,
                currency, reason, status, limit_label, requester_role, rfq_id,
                decided_by, decided_at, created_at, cart_items, rfq_ids,
                idempotency_key
            ) VALUES (
                $1, $2, $3, $4, $5, 'TJS', $6, $7::purchase_approval_status,
                $8, 'director', $9, $10, $11::timestamptz,
                NOW() - ($12::int * INTERVAL '1 day'), $13::jsonb, $14::jsonb, $15
            ) ON CONFLICT (requester_user_id, idempotency_key) WHERE idempotency_key IS NOT NULL DO NOTHING
            """,
            buyer_companies[index],
            buyer_user_ids[index],
            f"Согласование закупки №{index + 1}",
            SUPPLIER_NAMES[index],
            3500 + index * 2200,
            "Закупка для демонстрации процесса согласования.",
            ("wait", "approved", "rejected", "auto")[index % 4],
            ("до 10 000 TJS", "до 25 000 TJS")[index % 2],
            rfq_ids[index],
            admin_id if index % 4 in (1, 2) else None,
            _timestamp(index + 1) if index % 4 in (1, 2) else None,
            index + 1,
            json.dumps([{"title": ITEM_TITLES[index], "quantity": index + 1, "amount": 3500 + index * 2200}]),
            json.dumps([rfq_ids[index]]),
            f"presentation-approval-{index + 1}",
        )

    for index, contract_id in enumerate(contract_ids):
        await conn.execute(
            """
            INSERT INTO platform_payments (
                external_id, type, status, gateway, amount, commission,
                currency, title, description, actor_id, contract_id, metadata,
                paid_at, failed_at, created_at, updated_at
            ) VALUES (
                $1, $2::platform_payment_type, $3::platform_payment_status,
                'mock'::platform_payment_gateway, $4, $5, 'TJS', $6, $7, $8,
                $9, $10::jsonb, $11::timestamptz, $12::timestamptz,
                NOW() - ($13::int * INTERVAL '1 day'), NOW()
            )
            """,
            f"DEMO-PAY-{index + 1:03}",
            "commission" if index % 3 == 0 else "platform_revenue",
            ("paid", "pending", "failed")[index % 3],
            3500 + index * 1500,
            (3500 + index * 1500) * 0.05,
            f"Демо-платёж по договору №{index + 1}",
            "Транзакция создана для показа финансовых статусов.",
            supplier_actors[index + 1],
            contract_id,
            json.dumps({"demo": True, "sequence": index + 1}),
            _timestamp(index + 1) if index % 3 == 0 else None,
            _timestamp(index + 1) if index % 3 == 2 else None,
            index + 1,
        )
        destination_id = await conn.fetchval(
            """
            INSERT INTO withdrawal_destinations (actor_id, type, label, details, is_default)
            VALUES ($1, $2::withdrawal_destination_type, $3, $4, TRUE)
            RETURNING id
            """,
            supplier_actors[index + 1],
            "bank" if index % 2 == 0 else "wallet",
            "Демо-счёт •• 1234" if index % 2 == 0 else "Демо-кошелёк •• 5678",
            "Реквизиты для презентации, не предназначены для реальных выплат.",
        )
        withdrawal_id = await conn.fetchval(
            """
            INSERT INTO withdrawals (actor_id, destination_id, amount, currency, status, created_at, completed_at)
            VALUES ($1, $2, $3, 'TJS', $4::withdrawal_status, $5, $6)
            RETURNING id
            """,
            supplier_actors[index + 1],
            destination_id,
            1200 + index * 450,
            ("pending", "processing", "completed", "failed")[index % 4],
            _timestamp(index + 1),
            _timestamp(index + 1) if index % 4 == 2 else None,
        )
        invoice_id = await conn.fetchval(
            """
            INSERT INTO supplier_invoices (
                actor_id, contract_id, number, title, amount, currency,
                status, issued_at, due_at, paid_at
            ) VALUES (
                $1, $2, $3, $4, $5, 'TJS', $6::invoice_status,
                $7, $8, $9
            ) RETURNING id
            """,
            supplier_actors[index + 1],
            contract_id,
            f"DEMO-INV-{index + 1:03}",
            f"Счёт по договору №{index + 1}",
            3500 + index * 1500,
            ("issued", "paid", "overdue", "draft")[index % 4],
            _timestamp(index + 1),
            _timestamp(-14),
            _timestamp(index) if index % 4 == 1 else None,
        )
        await conn.execute(
            """
            INSERT INTO platform_payments (
                external_id, type, status, gateway, amount, commission,
                currency, title, description, actor_id, invoice_id,
                withdrawal_id, metadata, paid_at, created_at, updated_at
            ) VALUES (
                $1, 'payout'::platform_payment_type, $2::platform_payment_status,
                'mock'::platform_payment_gateway, $3, 0, 'TJS', $4, $5, $6,
                $7, $8, $9::jsonb, $10::timestamptz, $11, NOW()
            )
            """,
            f"DEMO-FIN-{index + 1:03}",
            ("paid", "pending", "failed")[index % 3],
            1200 + index * 450,
            f"Демо-выплата №{index + 1}",
            "Запись для показа списка выплат поставщика.",
            supplier_actors[index + 1],
            invoice_id,
            withdrawal_id,
            json.dumps({"demo": True, "sequence": index + 1}),
            _timestamp(index + 1) if index % 3 == 0 else None,
            _timestamp(index + 1),
        )
        plan = ("start", "pro", "business", "none")[index % 4]
        existing_subscription = await conn.fetchval(
            "SELECT id FROM supplier_subscriptions WHERE user_id = $1 LIMIT 1",
            supplier_user_ids[index],
        )
        if existing_subscription is None:
            await conn.execute(
                """
                INSERT INTO supplier_subscriptions (user_id, plan, active_until, created_at)
                VALUES ($1, $2::subscription_plan, $3, $4)
                """,
                supplier_user_ids[index],
                plan,
                _timestamp(-30) if plan != "none" else None,
                _timestamp(index),
            )
        await conn.execute(
            """
            INSERT INTO audit_logs (user_id, action, resource_type, resource_id, details, ip_address, created_at)
            VALUES ($1, $2, $3, $4, $5::jsonb, '127.0.0.1', $6)
            """,
            admin_id,
            ("contract.create", "proposal.submit", "payment.confirm")[index % 3],
            ("contract", "proposal", "payment")[index % 3],
            str(contract_id),
            json.dumps({"demo": True, "description": f"Демо-событие №{index + 1}"}),
            _timestamp(index + 1),
        )

    return {
        "demo_users_per_side": COUNT,
        "companies": COUNT * 2,
        "catalog_items": len(items),
        "rfqs": len(rfq_ids),
        "proposals": len(proposal_ids),
        "contracts": len(contract_ids),
        "reviews": 10,
        "notifications": COUNT,
        "reports_per_target": COUNT,
        "disputes": len(contract_ids),
        "support_tickets": COUNT,
        "purchase_approvals": COUNT,
        "platform_payments": len(contract_ids),
        "work_submissions": len(contract_ids),
        "invoices": len(contract_ids),
        "withdrawals": len(contract_ids),
    }


async def _seed_supplemental(conn) -> dict[str, int]:
    categories = await conn.fetch("SELECT id, slug FROM categories")
    category_ids = {row["slug"]: row["id"] for row in categories}
    company_names = [*BUYER_NAMES, *SUPPLIER_NAMES]
    company_category_links = 0
    for index, company_name in enumerate(company_names):
        company_id = await conn.fetchval(
            "SELECT id FROM companies WHERE title = $1",
            company_name,
        )
        category_id = category_ids.get(CATEGORY_SLUGS[index % COUNT])
        if company_id is None or category_id is None:
            continue
        await conn.execute(
            """
            INSERT INTO company_categories (company_id, category_id)
            SELECT $1, $2
            WHERE NOT EXISTS (
                SELECT 1 FROM company_categories
                WHERE company_id = $1 AND category_id = $2
            )
            """,
            company_id,
            category_id,
        )
        company_category_links += 1
        image_id = COMPANY_IMAGE_IDS[index % COUNT]
        company_image = _image_url(image_id, width=640)
        gallery = json.dumps(
            [
                {"src": company_image, "name": f"{company_name} — офис"},
                {"src": _image_url(IMAGE_IDS[index % COUNT], width=960), "name": "Наши проекты"},
            ],
            ensure_ascii=False,
        )
        await conn.execute(
            "UPDATE companies SET logo = $1 WHERE id = $2 AND (logo IS NULL OR logo = '')",
            company_image,
            company_id,
        )
        await conn.execute(
            "UPDATE company_profiles SET gallery = $1::jsonb WHERE company_id = $2 AND gallery = '[]'::jsonb",
            gallery,
            company_id,
        )

    for index, title in enumerate(ITEM_TITLES):
        item_id = await conn.fetchval(
            "SELECT id FROM catalog_items WHERE title = $1 ORDER BY id LIMIT 1",
            title,
        )
        if item_id is None:
            continue
        for image_order, image_index in enumerate((index, (index + 3) % COUNT)):
            image_url = _image_url(IMAGE_IDS[image_index])
            file_name = f"presentation-item-{index + 1:02}-{image_order + 1}.jpg"
            await conn.execute(
                """
                INSERT INTO item_media (item_id, file_name, file_url, media_type, sort_order)
                SELECT $1, $2::varchar(255), $3::varchar(500), 'image'::item_media_type, $4
                WHERE NOT EXISTS (
                    SELECT 1 FROM item_media WHERE item_id = $1 AND file_name = $2::varchar(255)
                )
                """,
                item_id,
                file_name,
                image_url,
                image_order,
            )

    for index, rfq_title in enumerate(RFQ_TITLES):
        rfq_id = await conn.fetchval(
            "SELECT id FROM rfqs WHERE title = $1 ORDER BY id LIMIT 1",
            rfq_title,
        )
        if rfq_id is None:
            continue
        file_name = f"presentation-rfq-{index + 1:02}.jpg"
        await conn.execute(
            """
            INSERT INTO rfq_attachments (id, rfq_id, file_name, file_url, file_type)
            SELECT $1::varchar(36), $2::varchar(36), $3::varchar(255), $4::varchar(500), 'image/jpeg'
            WHERE NOT EXISTS (
                SELECT 1 FROM rfq_attachments WHERE rfq_id = $2::varchar(36) AND file_name = $3::varchar(255)
            )
            """,
            str(uuid5(NAMESPACE_URL, f"brandmarket-presentation-rfq-image-{index + 1}")),
            rfq_id,
            file_name,
            _image_url(IMAGE_IDS[index]),
        )

    await conn.execute(
        """
        UPDATE work_submissions ws
        SET file_names = ARRAY['presentation-result.jpg'],
            assets = $1::jsonb
        FROM contracts c
        WHERE ws.contract_id = c.id
          AND c.title LIKE 'Договор по проекту %'
                    AND ws.file_names[1] LIKE 'demo-result-%.pdf'
        """,
        json.dumps(
            [{"kind": "file", "name": "presentation-result.jpg", "url": _image_url(IMAGE_IDS[0])}],
            ensure_ascii=False,
        ),
    )

    user_rows = await conn.fetch(
        """
        SELECT id, row_number() OVER (ORDER BY email) - 1 AS index
        FROM users
        WHERE email LIKE 'presentation.buyer.%@example.com'
           OR email LIKE 'presentation.supplier.%@example.com'
        ORDER BY email
        """
    )
    verification_statuses = ("pending", "needs_documents", "verified", "rejected")
    for row in user_rows:
        index = int(row["index"])
        status = verification_statuses[index % len(verification_statuses)]
        await conn.execute(
            """
            UPDATE users
            SET individual_verification_status = $2,
                individual_verification_reason = $3,
                individual_verification_submitted_at = $4,
                individual_verified_at = $5
            WHERE id = $1
            """,
            row["id"],
            status,
            "Нужно приложить документ с читаемыми реквизитами." if status == "needs_documents" else None,
            _timestamp(index + 1) if status != "none" else None,
            _timestamp(index) if status == "verified" else None,
        )
        document_status = "accepted" if status == "verified" else "rejected" if status == "rejected" else "uploaded"
        await conn.execute(
            """
            INSERT INTO user_documents (user_id, doc_type, file_name, file_url, status, uploaded_by, uploaded_at)
            SELECT $1, 'other', $2::varchar(255), $3, $4, $1, $5
            WHERE NOT EXISTS (
                SELECT 1 FROM user_documents
                WHERE user_id = $1 AND file_name = $2::varchar(255)
            )
            """,
            row["id"],
            f"presentation-document-{row['id']}.pdf",
            "/files/demo/presentation-document.pdf",
            document_status,
            _timestamp(index + 1),
        )

    for index in range(COUNT):
        order_id = f"DEMO-GW-{index + 1:03}"
        await conn.execute(
            """
            INSERT INTO payment_gateway_http_logs (
                gateway, direction, operation, order_id, method, url,
                request_headers, request_body, response_status,
                response_body, response_raw, error, created_at, responded_at
            )
            SELECT
                'mock', $1, $2, $3::varchar(64), 'POST', 'https://gateway.example.test/payments',
                '{"content-type":"application/json"}'::jsonb,
                $4::jsonb, $5, $6::jsonb, $7, $8, $9, $10
            WHERE NOT EXISTS (
                SELECT 1 FROM payment_gateway_http_logs WHERE order_id = $3::varchar(64)
            )
            """,
            "outbound" if index % 2 == 0 else "inbound",
            ("create_payment", "check_status", "callback")[index % 3],
            order_id,
            json.dumps({"demo": True, "amount": 1500 + index * 250}),
            (200, 202, 502)[index % 3],
            json.dumps({"ok": index % 3 != 2, "demo": True}),
            json.dumps({"ok": index % 3 != 2, "demo": True}, ensure_ascii=False),
            "Симулированный ответ шлюза для презентации." if index % 3 == 2 else None,
            _timestamp(index),
            _timestamp(index),
        )

    return {
        "individual_users": len(user_rows),
        "gateway_logs": COUNT,
        "company_category_links": company_category_links,
        "catalog_images": len(ITEM_TITLES) * 2,
        "company_images": len(company_names),
        "rfq_images": COUNT,
    }


async def seed_presentation() -> None:
    async with pool.connection() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", PRESENTATION_SEED_KEY)
            seeded = await conn.fetchval(
                "SELECT 1 FROM platform_settings WHERE key = $1",
                PRESENTATION_SEED_KEY,
            )
            if seeded:
                supplemental = await conn.fetchval(
                    "SELECT 1 FROM platform_settings WHERE key = $1",
                    PRESENTATION_SUPPLEMENT_KEY,
                )
                if supplemental:
                    print("Presentation data already seeded.")
                    return
                counts = await _seed_supplemental(conn)
                await conn.execute(
                    "INSERT INTO platform_settings (key, value) VALUES ($1, $2::jsonb)",
                    PRESENTATION_SUPPLEMENT_KEY,
                    json.dumps(counts),
                )
                print(f"Supplemental presentation data seeded: {counts}")
                return
            counts = await _seed(conn)
            supplemental = await _seed_supplemental(conn)
            counts.update(supplemental)
            await conn.execute(
                "INSERT INTO platform_settings (key, value) VALUES ($1, $2::jsonb)",
                PRESENTATION_SEED_KEY,
                json.dumps(counts),
            )
    print(f"Presentation data seeded: {counts}")