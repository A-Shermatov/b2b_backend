import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.security_core import hash_password
from app.database import pool
from app.db.schema import backfill_actors_if_needed, prepare_database
from app.modules.actors.services import ActorService

DEMO_ADMIN_EMAIL = "admin@example.com"
DEMO_BUYER_EMAIL = "buyer@example.com"
DEMO_SUPPLIER_EMAIL = "supplier@example.com"
DEMO_BOTH_EMAIL = "both@example.com"

LEGACY_EMAIL_MAP = {
    "admin@brandmarket.local": DEMO_ADMIN_EMAIL,
    "buyer@demo.local": DEMO_BUYER_EMAIL,
    "supplier@demo.local": DEMO_SUPPLIER_EMAIL,
}

CATEGORIES = [
    ("ИТ и разработка", "it", None),
    ("Веб-разработка", "web", "it"),
    ("Мобильные приложения", "mobile", "it"),
    ("Маркетинг и реклама", "marketing", None),
    ("SEO продвижение", "seo", "marketing"),
    ("Логистика и склад", "logistics", None),
    ("FTL перевозки", "ftl", "logistics"),
    ("Строительство и ремонт", "construction", None),
    ("Ремонт помещений", "renovation", "construction"),
]


async def migrate_legacy_emails(conn) -> None:
    for old_email, new_email in LEGACY_EMAIL_MAP.items():
        old_id = await conn.fetchval("SELECT id FROM users WHERE email = $1", old_email)
        if not old_id:
            continue
        conflict = await conn.fetchval("SELECT id FROM users WHERE email = $1", new_email)
        if conflict:
            continue
        await conn.execute("UPDATE users SET email = $1 WHERE id = $2", new_email, old_id)
        print(f"Migrated {old_email} -> {new_email}")


async def user_id_by_emails(conn, *emails: str) -> int | None:
    return await conn.fetchval(
        "SELECT id FROM users WHERE email = ANY($1::text[])",
        list(emails),
    )


async def insert_user(
    conn,
    *,
    email: str,
    password: str,
    first_name: str,
    last_name: str,
    role: str,
) -> dict:
    row = await conn.fetchrow(
        """
        INSERT INTO users (
            email, password_hash, first_name, last_name, role, status,
            created_at, updated_at
        )
        VALUES (
            $1, $2, $3, $4, $5::user_role, 'active'::user_status, NOW(), NOW()
        )
        RETURNING id, email, first_name, last_name, role::text AS role
        """,
        email,
        hash_password(password),
        first_name,
        last_name,
        role,
    )
    return dict(row)


async def insert_company(
    conn,
    *,
    title: str,
    actor_type: str,
    owner_id: int,
    legal_name: str | None = None,
    country: str | None = None,
    city: str | None = None,
    description: str | None = None,
    verification_status: str = "verified",
    rating: float = 0.0,
) -> dict:
    row = await conn.fetchrow(
        """
        INSERT INTO companies (
            title, actor_type, owner_id, legal_name, description,
            country, city, verification_status, operational_status, rating,
            created_at, updated_at
        )
        VALUES (
            $1, $2::actor_type, $3, $4, $5, $6, $7,
            $8::verification_status, 'active'::company_operational_status, $9,
            NOW(), NOW()
        )
        RETURNING id, title, actor_type::text AS actor_type,
                  verification_status::text AS verification_status
        """,
        title,
        actor_type,
        owner_id,
        legal_name,
        description,
        country,
        city,
        verification_status,
        rating,
    )
    return dict(row)


async def insert_company_extras(
    conn,
    company_id: int,
    owner_id: int,
    *,
    languages: list[str] | None = None,
    industries: list[str] | None = None,
    founded_year: int | None = None,
    employees_count: int | None = None,
    completed_contracts: int = 0,
    active_contracts: int = 0,
    average_rating: float = 0.0,
) -> None:
    await conn.execute(
        """
        INSERT INTO company_profiles (
            company_id, founded_year, employees_count, languages, industries
        )
        VALUES ($1, $2, $3, $4, $5)
        """,
        company_id,
        founded_year,
        employees_count,
        languages or [],
        industries or [],
    )
    await conn.execute(
        """
        INSERT INTO company_stats (
            company_id, completed_contracts, active_contracts,
            disputes_count, average_rating
        )
        VALUES ($1, $2, $3, 0, $4)
        """,
        company_id,
        completed_contracts,
        active_contracts,
        average_rating,
    )
    await conn.execute(
        """
        INSERT INTO company_users (company_id, user_id, role)
        VALUES ($1, $2, 'director'::company_role)
        """,
        company_id,
        owner_id,
    )


async def seed_categories(conn) -> None:
    existing = await conn.fetchval("SELECT id FROM categories LIMIT 1")
    if existing:
        return
    slug_to_id: dict[str, int] = {}
    for name, slug, parent_slug in CATEGORIES:
        parent_id = slug_to_id.get(parent_slug) if parent_slug else None
        row = await conn.fetchrow(
            """
            INSERT INTO categories (parent_id, name, slug)
            VALUES ($1, $2, $3)
            RETURNING id
            """,
            parent_id,
            name,
            slug,
        )
        slug_to_id[slug] = row["id"]
    print(f"Seeded {len(CATEGORIES)} categories")


async def seed() -> None:
    await prepare_database()
    svc = ActorService()

    async with pool.connection() as conn:
        await migrate_legacy_emails(conn)
        await seed_categories(conn)

        if not await user_id_by_emails(conn, DEMO_ADMIN_EMAIL, "admin@brandmarket.local"):
            await insert_user(
                conn,
                email=DEMO_ADMIN_EMAIL,
                password="Admin123!",
                first_name="Platform",
                last_name="Admin",
                role="admin",
            )
            print(f"Seeded admin: {DEMO_ADMIN_EMAIL} / Admin123!")

    buyer_id = None
    async with pool.connection() as conn:
        buyer_id = await user_id_by_emails(conn, DEMO_BUYER_EMAIL, "buyer@demo.local")
    if not buyer_id:
        async with pool.connection() as conn:
            buyer = await insert_user(
                conn,
                email=DEMO_BUYER_EMAIL,
                password="Buyer123!",
                first_name="Иван",
                last_name="Заказчик",
                role="buyer",
            )
        await svc.ensure_individual_actor(buyer, "buyer")
        async with pool.connection() as conn:
            company = await insert_company(
                conn,
                title="ООО Демо Заказчик",
                actor_type="buyer",
                owner_id=buyer["id"],
                legal_name="ООО Демо Заказчик",
                country="Таджикистан",
                city="Душанбе",
            )
            await insert_company_extras(
                conn,
                company["id"],
                buyer["id"],
                languages=["ru"],
                industries=["IT"],
            )
        await svc.ensure_company_actor(company, "buyer")
        print(f"Seeded buyer: {DEMO_BUYER_EMAIL} / Buyer123! (company id={company['id']})")

    supplier_id = None
    async with pool.connection() as conn:
        supplier_id = await user_id_by_emails(
            conn, DEMO_SUPPLIER_EMAIL, "supplier@demo.local"
        )
    if not supplier_id:
        async with pool.connection() as conn:
            supplier = await insert_user(
                conn,
                email=DEMO_SUPPLIER_EMAIL,
                password="Supplier123!",
                first_name="Пётр",
                last_name="Исполнитель",
                role="supplier",
            )
        await svc.ensure_individual_actor(supplier, "supplier")
        async with pool.connection() as conn:
            company = await insert_company(
                conn,
                title="ТехноСнаб",
                actor_type="supplier",
                owner_id=supplier["id"],
                legal_name="ООО «ТехноСнаб»",
                country="Таджикистан",
                city="Душанбе",
                description="Исполнитель IT-оборудования",
                rating=4.7,
            )
            await insert_company_extras(
                conn,
                company["id"],
                supplier["id"],
                founded_year=2015,
                employees_count=45,
                languages=["ru", "en"],
                industries=["IT"],
                completed_contracts=28,
                active_contracts=3,
                average_rating=4.7,
            )
        await svc.ensure_company_actor(company, "supplier")
        print(
            f"Seeded supplier: {DEMO_SUPPLIER_EMAIL} / Supplier123! "
            f"(company id={company['id']})"
        )

    both_id = None
    async with pool.connection() as conn:
        both_id = await user_id_by_emails(conn, DEMO_BOTH_EMAIL)
    if not both_id:
        async with pool.connection() as conn:
            both = await insert_user(
                conn,
                email=DEMO_BOTH_EMAIL,
                password="Both123!",
                first_name="Алекс",
                last_name="Универсал",
                role="both",
            )
        await svc.ensure_individual_actors_for_user(both)
        async with pool.connection() as conn:
            buyer_co = await insert_company(
                conn,
                title="ООО Универсал Закупки",
                actor_type="buyer",
                owner_id=both["id"],
                country="Таджикистан",
                city="Душанбе",
            )
            await insert_company_extras(conn, buyer_co["id"], both["id"])
        await svc.ensure_company_actor(buyer_co, "buyer")
        async with pool.connection() as conn:
            supplier_co = await insert_company(
                conn,
                title="ООО Универсал Поставки",
                actor_type="supplier",
                owner_id=both["id"],
                country="Таджикистан",
                city="Душанбе",
                verification_status="pending",
            )
            await insert_company_extras(conn, supplier_co["id"], both["id"])
        await svc.ensure_company_actor(supplier_co, "supplier")
        print(f"Seeded both: {DEMO_BOTH_EMAIL} / Both123!")

    await backfill_actors_if_needed()
    print("Seed complete.")
    await pool.close()


if __name__ == "__main__":
    asyncio.run(seed())
